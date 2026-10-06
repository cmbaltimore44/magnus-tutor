// Markdown with KaTeX math. Models write \( \) and \[ \] as often as $ and $$,
// so both are normalized. Citations like [Textbook ch. 5.3, p. 142] that match
// a known source become clickable chips that open the page in the Sources tab.
import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import type { Source } from '../types';

function normalizeMath(s: string): string {
  return s
    // $$…$$ on one line inside a paragraph would render inline; make it a display block.
    .replace(/\$\$([^\n$]+?)\$\$/g, (_, m) => `\n$$\n${m.trim()}\n$$\n`)
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `\n$$\n${m.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m.trim()}$`);
}

function linkCitations(s: string, sources?: Source[]): string {
  if (!sources?.length) return s;
  let out = s;
  sources.forEach((src, i) => {
    const esc = src.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    out = out.replace(new RegExp(`\\[(?:${esc}|S${i + 1})\\](?!\\()`, 'g'), `[${src.label}](#src-${i})`);
  });
  return out;
}

type Props = { text: string; sources?: Source[]; onCite?: (i: number) => void; className?: string };

function MarkdownImpl({ text, sources, onCite, className }: Props) {
  return (
    <div className={`md ${className ?? ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith('#src-')) {
              const i = Number(href.slice(5));
              return (
                <button type="button" className="cite" onClick={() => onCite?.(i)} title="Show this page">
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(normalizeMath(text), sources)}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
