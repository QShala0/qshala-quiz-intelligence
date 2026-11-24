import { SourceCatalog } from './types';

export const FIXED_VOCABULARY: readonly string[] = [
  'Connect',
  '3 clue',
  '3 Clue',
  'List It',
  'Arrange in order',
  'Image Reveal',
  'Agastya',
  'Bengaluru',
  'Chandrayaan',
  'QShala',
  'Forrest Gump',
];

/**
 * Common phonetic / speech recognizer mistakes mapped to preferred spelling
 */
export const COMMON_PHONETIC_CORRECTIONS: Record<string, string> = {
  'connect for': 'Connect',
  'connect four': 'Connect',
  'connect 4': 'Connect',
  'step clue': '3 clue',
  'step clues': '3 clue',
  'stepped clues': '3 clue',
  'stepped clue': '3 clue',
  'step-clue': '3 clue',
  'three clue': '3 clue',
  'three clues': '3 clue',
  '3 clue': '3 clue',
  '3 clues': '3 clue',
  'list it': 'List It',
  'listed': 'List It',
  'listit': 'List It',
  'arrange in order': 'Arrange in order',
  'arranged in order': 'Arrange in order',
  'image reveal': 'Image Reveal',
  'image reveals': 'Image Reveal',
  'agastya': 'Agastya',
  'agasthya': 'Agastya',
  'augustya': 'Agastya',
  'agasty': 'Agastya',
  'bengaluru': 'Bengaluru',
  'bangalore': 'Bengaluru',
  "bangalore's": 'Bengaluru',
  'bangalore’s': 'Bengaluru',
  'banglore': 'Bengaluru',
  'bengalore': 'Bengaluru',
  'chandrayaan': 'Chandrayaan',
  'chandra yaan': 'Chandrayaan',
  'chandrayan': 'Chandrayaan',
  'chandraayan': 'Chandrayaan',
  'qshala': 'QShala',
  'q shala': 'QShala',
  'kushala': 'QShala',
  'cushala': 'QShala',
  'qushala': 'QShala',
  'q-shala': 'QShala',
  'forest gump': 'Forrest Gump',
  'forest gum': 'Forrest Gump',
  'forrest gum': 'Forrest Gump',
  'forrest gump': 'Forrest Gump',
};

const PROTECTED_ENGLISH_WORDS = new Set<string>([
  'quiz',
  'on',
  'about',
  'history',
  'questions',
  'question',
  'make',
  'it',
  'harder',
  'easier',
  'add',
  'grade',
  'class',
  'finals',
  'prelims',
  'round',
  'what',
  'which',
  'where',
  'who',
  'how',
  'when',
  'the',
  'and',
  'for',
  'with',
  'from',
  'in',
  'of',
  'to',
  'a',
  'an',
  'some',
  'more',
  'give',
  'me',
  'show',
  'search',
]);

/**
 * Standard Levenshtein distance implementation without external dependencies
 */
export function levenshteinDistance(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;

  const d: number[][] = [];
  for (let i = 0; i <= m; i++) {
    d[i] = [i];
  }
  for (let j = 0; j <= n; j++) {
    d[0][j] = j;
  }

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(
        d[i - 1][j] + 1, // deletion
        d[i][j - 1] + 1, // insertion
        d[i - 1][j - 1] + cost // substitution
      );
    }
  }
  return d[m][n];
}

/**
 * Builds an aggregated vocabulary list from existing source catalog data + fixed list
 */
export function buildVocabulary(catalog?: SourceCatalog | null): string[] {
  const vocabSet = new Set<string>(FIXED_VOCABULARY);
  if (catalog) {
    if (Array.isArray(catalog.series)) {
      for (const s of catalog.series) {
        if (s.label && s.label.trim().length > 0) {
          vocabSet.add(s.label.trim());
        }
      }
    }
    if (Array.isArray(catalog.decks)) {
      for (const d of catalog.decks) {
        if (d.title && d.title.trim().length > 0) {
          const clean = d.title.replace(/\.(?:pptx|ppt)$/i, '').trim();
          if (clean) vocabSet.add(clean);
        }
        if (d.filename && d.filename.trim().length > 0) {
          const clean = d.filename.replace(/\.(?:pptx|ppt)$/i, '').trim();
          if (clean) vocabSet.add(clean);
        }
      }
    }
  }
  return Array.from(vocabSet);
}

/**
 * Count how many known vocabulary items occur in a given candidate transcript
 */
export function countVocabMatches(text: string, vocab: readonly string[]): number {
  if (!text || !text.trim() || vocab.length === 0) return 0;
  const lower = text.toLowerCase();
  let count = 0;
  for (const item of vocab) {
    const itemLower = item.toLowerCase();
    if (lower.includes(itemLower)) {
      count++;
    }
  }
  return count;
}

/**
 * Searches for a close match to vocabulary list or phonetic corrections
 */
function findBestVocabMatch(phrase: string, vocabList: readonly string[]): string | null {
  const pLower = phrase.toLowerCase().trim();
  if (PROTECTED_ENGLISH_WORDS.has(pLower)) {
    return null;
  }

  // Direct check in hand-crafted phonetic corrections
  if (COMMON_PHONETIC_CORRECTIONS[pLower]) {
    return COMMON_PHONETIC_CORRECTIONS[pLower];
  }

  let bestMatch: string | null = null;
  let minDistance = Infinity;

  for (const item of vocabList) {
    const itemLower = item.toLowerCase();
    if (pLower === itemLower) {
      return item;
    }

    const maxLen = Math.max(pLower.length, itemLower.length);
    const diffLen = Math.abs(pLower.length - itemLower.length);

    // Guardrail: don't compare words with vastly different lengths
    if (diffLen > 2) continue;

    let allowedDist = 0;
    if (maxLen >= 7) {
      allowedDist = 2;
    } else if (maxLen >= 5) {
      allowedDist = 1;
    }

    if (allowedDist === 0) continue;

    const dist = levenshteinDistance(pLower, itemLower);
    if (dist <= allowedDist && dist < minDistance) {
      const similarity = 1 - dist / maxLen;
      if (similarity >= 0.75) {
        minDistance = dist;
        bestMatch = item;
      }
    }
  }

  return bestMatch;
}

/**
 * Replaces words or two-word phrases that closely match vocabulary items
 */
export function correctTranscript(text: string, vocab?: readonly string[]): string {
  if (!text || !text.trim()) return text;
  const vocabList = vocab && vocab.length > 0 ? vocab : FIXED_VOCABULARY;

  let current = text;
  // Step 1: Replace known multi-word corrections from dictionary
  for (const [mistake, correct] of Object.entries(COMMON_PHONETIC_CORRECTIONS)) {
    const regex = new RegExp(`\\b${mistake.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'gi');
    current = current.replace(regex, correct);
  }

  // Step 2: Split by whitespace while preserving tokens
  const tokens = current.split(/(\s+)/);
  const words: string[] = [];
  const separators: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    if (i % 2 === 0) words.push(tokens[i]);
    else separators.push(tokens[i]);
  }

  const resultWords = [...words];
  let i = 0;
  while (i < resultWords.length) {
    // Check 2-word phrase
    if (i < resultWords.length - 1) {
      const twoWords = `${resultWords[i]} ${resultWords[i + 1]}`.replace(/[.,/#!$%^&*;:{}=\-_`~()]/g, '');
      const match = findBestVocabMatch(twoWords, vocabList);
      if (match) {
        resultWords[i] = match;
        resultWords.splice(i + 1, 1);
        if (i < separators.length) separators.splice(i, 1);
        i++;
        continue;
      }
    }

    // Check single word
    const singleWord = resultWords[i].replace(/[.,/#!$%^&*;:{}=\-_`~()]/g, '');
    const match = findBestVocabMatch(singleWord, vocabList);
    if (match) {
      const puncMatch = resultWords[i].match(/[.,!?;:]+$/);
      resultWords[i] = match + (puncMatch ? puncMatch[0] : '');
    }
    i++;
  }

  // Reassemble tokens
  let reassembled = '';
  for (let j = 0; j < resultWords.length; j++) {
    reassembled += resultWords[j];
    if (j < separators.length) reassembled += separators[j];
  }

  return reassembled;
}

/**
 * Format transcript for search mode (lowercase + no punctuation) or prompt mode
 */
export function formatTranscriptForMode(transcript: string, mode: 'prompt' | 'search'): string {
  if (mode === 'search') {
    return transcript
      .replace(/[^\w\s]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }
  return transcript.trim();
}
