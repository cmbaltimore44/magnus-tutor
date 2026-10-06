// Hand-drawn-ish line icons (1.5px strokes) so the UI doesn't look like a stock kit.
type P = { size?: number; className?: string };
const S = ({ size = 18, className, children }: P & { children: React.ReactNode }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
    {children}
  </svg>
);
export const Feather = (p: P) => (<S {...p}><path d="M20 4c-6 0-12 4-14 12l-2 4" /><path d="M8 14c4 0 7-1 9-4" /><path d="M11 10h6" /></S>);
export const Send = (p: P) => (<S {...p}><path d="M4 12l16-8-6 16-3-6-7-2z" /></S>);
export const Image = (p: P) => (<S {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="M21 16l-5-5-8 8" /></S>);
export const Play = (p: P) => (<S {...p}><path d="M7 5l12 7-12 7z" /></S>);
export const Pause = (p: P) => (<S {...p}><path d="M8 5v14M16 5v14" /></S>);
export const Skip = (p: P) => (<S {...p}><path d="M6 5l9 7-9 7zM18 5v14" /></S>);
export const Stop = (p: P) => (<S {...p}><rect x="6" y="6" width="12" height="12" rx="1.5" /></S>);
export const Plus = (p: P) => (<S {...p}><path d="M12 5v14M5 12h14" /></S>);
export const Book = (p: P) => (<S {...p}><path d="M4 5a2 2 0 012-2h12v16H6a2 2 0 00-2 2z" /><path d="M4 19V5" /></S>);
export const Gear = (p: P) => (<S {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></S>);
export const Quill = (p: P) => (<S {...p}><path d="M4 20l6-6M14 4l6 6-8 8H6v-6z" /></S>);
export const Code = (p: P) => (<S {...p}><path d="M8 7l-5 5 5 5M16 7l5 5-5 5" /></S>);
export const Pages = (p: P) => (<S {...p}><path d="M7 3h8l4 4v14H7z" /><path d="M15 3v4h4M10 12h6M10 16h6" /></S>);
export const Search = (p: P) => (<S {...p}><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></S>);
export const Check = (p: P) => (<S {...p}><path d="M5 12l5 5 9-10" /></S>);
export const X = (p: P) => (<S {...p}><path d="M6 6l12 12M18 6L6 18" /></S>);
export const Lock = (p: P) => (<S {...p}><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></S>);
export const Cloud = (p: P) => (<S {...p}><path d="M7 18h10a4 4 0 00.5-8A6 6 0 006 9a4.5 4.5 0 001 9z" /></S>);
export const Spark = (p: P) => (<S {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6" /></S>);
export const Archive = (p: P) => (<S {...p}><rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10h14V9M10 13h4" /></S>);
export const Refresh = (p: P) => (<S {...p}><path d="M20 11a8 8 0 10-2.3 5.7M20 5v6h-6" /></S>);
export const Expand = (p: P) => (<S {...p}><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" /></S>);
