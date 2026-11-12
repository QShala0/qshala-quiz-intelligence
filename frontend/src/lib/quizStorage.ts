import { Quiz } from './types';

const STORAGE_KEY = 'qshala_generated_quizzes';

/**
 * Retrieves all locally saved quizzes from localStorage.
 */
export function getLocalQuizzes(): Quiz[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    console.error('Error reading quizzes from localStorage:', err);
    return [];
  }
}

/**
 * Saves or updates a quiz in localStorage so it persists across serverless container restarts.
 */
export function saveLocalQuiz(quiz: Quiz): void {
  if (typeof window === 'undefined' || !quiz || !quiz.id) return;
  try {
    const current = getLocalQuizzes();
    const existingIndex = current.findIndex((q) => q.id === quiz.id);
    let updated: Quiz[];
    if (existingIndex >= 0) {
      updated = [...current];
      updated[existingIndex] = { ...updated[existingIndex], ...quiz };
    } else {
      updated = [quiz, ...current];
    }
    // Cap at 50 recent quizzes to stay well within localStorage's ~5MB limit
    if (updated.length > 50) {
      updated = updated.slice(0, 50);
    }
    const serialized = JSON.stringify(updated);
    try {
      localStorage.setItem(STORAGE_KEY, serialized);
    } catch (storageErr) {
      // QuotaExceededError: shed oldest quizzes until it fits
      let trimmed = updated;
      while (trimmed.length > 1) {
        trimmed = trimmed.slice(0, Math.floor(trimmed.length * 0.75));
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
          return;
        } catch { /* keep trimming */ }
      }
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (err) {
    console.error('Error saving quiz to localStorage:', err);
  }
}

/**
 * Retrieves a specific quiz from localStorage by its ID.
 */
export function getLocalQuiz(id: string): Quiz | null {
  if (typeof window === 'undefined' || !id) return null;
  const list = getLocalQuizzes();
  return list.find((q) => q.id === id) || null;
}

/**
 * Deletes a quiz from localStorage by its ID.
 */
export function deleteLocalQuiz(id: string): void {
  if (typeof window === 'undefined' || !id) return;
  try {
    const current = getLocalQuizzes();
    const filtered = current.filter((q) => q.id !== id);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  } catch (err) {
    console.error('Error deleting quiz from localStorage:', err);
  }
}

/**
 * Makes the browser copy follow the server: quizzes the server no longer lists are dropped (so a quiz a colleague
 * deleted does not come back), and server rows keep any full question list this browser already cached.
 * Returns the server's list, newest first.
 */
export function syncLocalQuizzesWithServer(serverQuizzes: Quiz[]): Quiz[] {
  const local = new Map(getLocalQuizzes().map((q) => [q.id, q] as const));
  const result = serverQuizzes
    .filter((q) => q && q.id)
    .map((sq) => {
      const lq = local.get(sq.id);
      return lq && (!sq.questions || sq.questions.length === 0) && lq.questions?.length
        ? { ...sq, questions: lq.questions }
        : sq;
    })
    .sort((a, b) => (b.created_at ? new Date(b.created_at).getTime() : 0) - (a.created_at ? new Date(a.created_at).getTime() : 0));
  if (typeof window !== 'undefined') {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(result.slice(0, 50)));
    } catch {
      // storage full: the server stays the source of truth, so losing this cache is harmless
    }
  }
  return result;
}

/**
 * Seamlessly merges backend database quizzes with local browser-persisted quizzes,
 * deduplicating by ID and sorting newest first.
 */
export function mergeQuizzes(backendQuizzes: Quiz[], localQuizzes: Quiz[]): Quiz[] {
  const map = new Map<string, Quiz>();

  // Add backend quizzes first
  for (const bq of backendQuizzes) {
    if (bq && bq.id) {
      map.set(bq.id, bq);
    }
  }

  // Overlay local quizzes
  for (const lq of localQuizzes) {
    if (lq && lq.id) {
      const existing = map.get(lq.id);
      if (!existing) {
        map.set(lq.id, lq);
      } else {
        map.set(lq.id, {
          ...existing,
          ...lq,
          questions: (lq.questions && lq.questions.length > 0) ? lq.questions : existing.questions,
        });
      }
    }
  }

  // Sort newest first
  return Array.from(map.values()).sort((a, b) => {
    const tA = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tB = b.created_at ? new Date(b.created_at).getTime() : 0;
    return tB - tA;
  });
}
