import { Fragment } from 'react';

// Highlights the words typed in the search box inside a piece of text. Mirrors the server's exact-words search:
// a word matches from the start of a word ("astro" lights up "Astronomy"), "quoted phrases" match as a phrase.
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function searchTerms(query: string | undefined | null): string[] {
  const out: string[] = [];
  const re = /"([^"]+)"|([\p{L}\p{N}_]+)/gu;
  for (const m of (query || '').matchAll(re)) {
    const t = (m[1] ?? m[2] ?? '').trim();
    if (t) out.push(t);
  }
  return out;
}

export function buildHighlightRegex(query: string | undefined | null): RegExp | null {
  const terms = searchTerms(query).sort((a, b) => b.length - a.length);
  if (terms.length === 0) return null;
  const parts = terms.map((t) => `(?<![\\p{L}\\p{N}_])${escapeRe(t).replace(/\s+/g, '\\s+')}`);
  return new RegExp(`(${parts.join('|')})`, 'giu');
}

export default function Highlight({ text, query }: { text: string | null | undefined; query?: string | null }) {
  const value = text ?? '';
  const re = buildHighlightRegex(query);
  if (!re || !value) return <>{value}</>;
  const pieces = value.split(re);
  return (
    <>
      {pieces.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-sm bg-highlight px-0.5 text-slate-900">{p}</mark>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}
