export type Course = {
  slug: string;
  name: string;
  code: string;
  title: string;
  short: string;
  instructor: string;
  term: string;
  status: 'active' | 'archived';
  kind: string[];
  description: string;
  topics: string[];
  notation_conventions: string;
  folders: Record<string, string>;
  languages: string[];
  is_writing: boolean;
  schedule?: string;
  grading?: string;
};

export type Message = {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  images: string[];
  meta: Record<string, any>;
  created_at: number;
};

export type Problem = {
  id: number;
  text: string;
  kind: string;
  confidence: 'verified' | 'agreed' | 'uncertain' | null;
  solver_status: 'pending' | 'running' | 'paused' | 'done' | 'failed' | 'skipped';
  solver_meta: Record<string, any>;
  reference_solution: any;
  source: string;
};

export type Session = {
  id: number;
  course: string | null;
  mode: 'office_hours' | 'ask' | 'code' | 'quiz';
  title: string | null;
  started_at: number;
  ended_at: number | null;
  state: Record<string, any>;
  message_count?: number;
  first_message?: string;
  focus_label?: string | null;
};

export type Source = {
  label: string;
  document_id?: number;
  page_index?: number;
  printed_page?: string;
  chunk_id?: number;
  section_path?: string;
  kind?: string;
  text?: string;
};

export type Timer = {
  active: boolean;
  phase?: 'focus' | 'short' | 'long';
  status?: 'running' | 'ended';
  title?: string | null;
  label?: string | null;
  round?: number;
  every?: number;
  paused?: boolean;
  remaining_ms?: number;
  ends_at?: number | null;
  phase_label?: string;
  computed_at?: number;
  alert_owner?: 'terminal' | 'web' | 'both';
  tui_alive?: boolean;
  available?: boolean;
  error?: string;
};
