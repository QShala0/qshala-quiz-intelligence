'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import {
  Plus,
  FileJson,
  FileSpreadsheet,
  Presentation,
  ArrowRight,
  Trash2,
  Pencil,
} from 'lucide-react';
import { api, downloadQuizFile, ExportOptions } from '../../lib/api';
import ExportQuizModal from '../../components/ExportQuizModal';
import TableSkeleton from '../../components/TableSkeleton';
import { Quiz } from '../../lib/types';
import { getLocalQuizzes, deleteLocalQuiz, syncLocalQuizzesWithServer } from '../../lib/quizStorage';
import LoadErrorBanner from '../../components/LoadErrorBanner';
import { useConfirm, useToast, QT } from '../../components/ui';

export default function QuizzesLibraryPage() {
  const confirm = useConfirm();
  const toast = useToast();
  const [quizzes, setQuizzes] = useState<Quiz[]>([]);
  const [loading, setLoading] = useState(true);
  const [exportingQuiz, setExportingQuiz] = useState<Quiz | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [offlineCopy, setOfflineCopy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [savingId, setSavingId] = useState<string | null>(null);
  const savingRef = useRef(false);
  const cancelledRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSave = async (quizId: string) => {
    if (savingRef.current) return;
    const trimmed = editTitle.trim();
    const currentQuiz = quizzes.find((q) => q.id === quizId);
    if (!currentQuiz) {
      setEditingId(null);
      return;
    }
    if (trimmed === currentQuiz.title) {
      setEditingId(null);
      return;
    }
    if (!trimmed) {
      toast.error('Every quiz needs a name. Give it one?');
      setEditingId(null);
      return;
    }
    if (trimmed.length > 255) {
      toast.error('That name is a bit long. Keep it under 255 characters.');
      return;
    }
    savingRef.current = true;
    setSavingId(quizId);
    try {
      await api.renameQuiz(quizId, trimmed);
      setQuizzes((prev) =>
        prev.map((q) => (q.id === quizId ? { ...q, title: trimmed } : q))
      );
      toast.success('Renamed, fresh and tidy.');
      setEditingId(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Could not rename';
      toast.error(msg);
    } finally {
      savingRef.current = false;
      setSavingId(null);
    }
  };

  // The server is the single source of truth. The copy kept in this browser is only a read-only fallback for when the
  // server cannot be reached, and it is labelled as such.
  const fetchQuizzes = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const serverQuizzes = await api.listQuizzes();
      setQuizzes(syncLocalQuizzesWithServer(serverQuizzes));
      setOfflineCopy(false);
    } catch (err) {
      console.error('Could not load quizzes from the server:', err);
      const cached = getLocalQuizzes();
      setQuizzes(cached);
      setOfflineCopy(cached.length > 0);
      setLoadError(err instanceof Error && err.message ? err.message : 'The server could not be reached.');
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteQuiz = async (quizId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!(await confirm({ title: 'Delete this quiz?', message: 'It will be removed for everyone. This cannot be undone.', danger: true }))) return;
    setActionError(null);
    try {
      await api.deleteQuiz(quizId);
    } catch (err) {
      console.error('Failed to delete quiz:', err);
      setActionError(`The quiz was not deleted: ${err instanceof Error && err.message ? err.message : 'the server could not be reached.'}`);
      return;
    }
    deleteLocalQuiz(quizId);
    setQuizzes((prev) => prev.filter((q) => q.id !== quizId));
  };

  const handleDownloadQuiz = async (quiz: Quiz, format: 'json' | 'csv' | 'pptx', e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await downloadQuizFile(quiz, format);
    } catch (err) {
      console.warn('Direct export failed, navigating to export URL:', err);
      window.location.href = api.exportQuizUrl(quiz.id, format);
    }
  };

  useEffect(() => {
    fetchQuizzes();
  }, []);

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b-2 border-sun pb-5">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-[28px] sm:text-[32px] font-black tracking-tight text-ink leading-tight">
              Generated Quizzes
            </h1>
            <QT
              pose="professor"
              height={34}
              interactive={true}
              motion="breathe"
              speechBubble="Every quiz in your library is calibrated for tournament play!"
              bubblePosition="top-right"
              alt="Professor QT"
            />
          </div>
          <p className="mt-1 text-[14px] text-slate-500 font-normal leading-relaxed">
            Browse, review, edit, and export generated quizzes.
          </p>
        </div>

        <Link
          href="/generate"
          className="flex items-center gap-1.5 rounded-xl bg-sun px-4 py-2 text-[13.5px] font-bold text-ink hover:brightness-105 active:scale-[0.99] transition-all shadow-sticker-sm cursor-pointer"
        >
          <Plus className="h-4 w-4" />
          <span>New Quiz</span>
        </Link>
      </div>

      {loadError && !loading && (
        <LoadErrorBanner
          title="Could not load the quiz library from the server."
          message={loadError}
          note={offlineCopy ? 'Showing the copy saved in this browser. It may be out of date, and changes made while the server is unreachable are not shared.' : undefined}
          onRetry={fetchQuizzes}
        />
      )}
      {actionError && <LoadErrorBanner title="Something went wrong." message={actionError} />}

      {/* Clean Table List of Quizzes */}
      <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
        {loading ? (
          <TableSkeleton columns={7} />
        ) : (
          <table className="w-full text-left text-[13px] border-collapse">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                <th className="py-3 px-4">Quiz Title</th>
                <th className="py-3 px-4">Topic</th>
                <th className="py-3 px-4">Format</th>
                <th className="py-3 px-4">Questions</th>
                <th className="py-3 px-4">Difficulty</th>
                <th className="py-3 px-4">Created</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {quizzes.map((quiz) => (
                <tr key={quiz.id} className="hover:bg-slate-50/70 transition-colors group">
                  <td className="py-3.5 px-4 font-semibold text-[14px] text-slate-900">
                    {editingId === quiz.id ? (
                      <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                        <input
                          ref={inputRef}
                          type="text"
                          value={editTitle}
                          disabled={savingId === quiz.id}
                          onChange={(e) => setEditTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              handleSave(quiz.id);
                            } else if (e.key === 'Escape') {
                              e.preventDefault();
                              cancelledRef.current = true;
                              setEditingId(null);
                            }
                          }}
                          onBlur={() => {
                            if (cancelledRef.current || savingRef.current) return;
                            handleSave(quiz.id);
                          }}
                          className="w-full max-w-sm rounded border border-blue-400 bg-white px-2 py-1 text-[13px] font-medium text-slate-900 shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:bg-slate-50 disabled:opacity-60"
                          autoFocus
                          onFocus={(e) => e.target.select()}
                        />
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 group/title">
                        <Link
                          href={`/quizzes/${quiz.id}`}
                          className="hover:text-blue-600 transition-colors"
                        >
                          <span className="line-clamp-2" title={quiz.raw_prompt && quiz.raw_prompt !== quiz.title ? `${quiz.title}\n\nOriginal prompt: ${quiz.raw_prompt}` : quiz.title}>{quiz.title}</span>
                        </Link>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            cancelledRef.current = false;
                            setEditingId(quiz.id);
                            setEditTitle(quiz.title);
                          }}
                          className="opacity-60 group-hover/title:opacity-100 hover:opacity-100 p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded transition-all cursor-pointer"
                          title="Rename"
                          aria-label="Rename quiz"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="py-3.5 px-4 text-slate-600">{quiz.topic}</td>
                  <td className="py-3.5 px-4 text-slate-600">
                    <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-[11.5px] font-semibold text-slate-700">
                      {quiz.quiz_format === 'STAGE_FINALS' ? '⚡ Stage Finals' :
                       quiz.quiz_format === 'CLASSROOM_WARMUP' ? '💡 Classroom Warmup' :
                       quiz.quiz_format === 'THEMATIC_SPECIAL' ? '🎯 Thematic' :
                       quiz.quiz_format === 'GENERAL_CHAMPIONSHIP' ? '🌍 General Mixed Bag' :
                       '🏆 Tournament Prelims'}
                    </span>
                  </td>
                  <td className="py-3.5 px-4 text-slate-600 font-medium">
                    {quiz.question_count} Qs
                  </td>
                  <td className="py-3.5 px-4 text-slate-600">{quiz.difficulty}</td>
                  <td className="py-3.5 px-4 text-slate-400 text-[12px] font-normal">
                    {quiz.created_at ? new Date(quiz.created_at).toLocaleDateString() : 'Recent'}
                  </td>
                  <td className="py-3.5 px-4 text-right">
                    <div className="inline-flex items-center gap-2 opacity-90 group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={(e) => handleDownloadQuiz(quiz, 'json', e)}
                        className="rounded p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
                        title="Download JSON"
                      >
                        <FileJson className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => handleDownloadQuiz(quiz, 'csv', e)}
                        className="rounded p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
                        title="Download CSV"
                      >
                        <FileSpreadsheet className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setExportingQuiz(quiz);
                        }}
                        className="rounded p-1 text-slate-400 hover:text-orange-600 hover:bg-orange-50 transition-colors cursor-pointer"
                        title="Download Tournament PPTX"
                      >
                        <Presentation className="h-3.5 w-3.5" />
                      </button>
                      <Link
                        href={`/quizzes/${quiz.id}`}
                        className="inline-flex items-center gap-1 font-semibold text-[12px] text-blue-600 hover:text-blue-700 pl-2"
                      >
                        <span>Open</span>
                        <ArrowRight className="h-3 w-3" />
                      </Link>
                      <button
                        type="button"
                        onClick={(e) => handleDeleteQuiz(quiz.id, e)}
                        className="rounded p-1 text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors ml-1 cursor-pointer"
                        title="Delete Quiz"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}

              {quizzes.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-[13px] text-slate-400 font-normal">
                    <div className="flex flex-col items-center justify-center space-y-2">
                      <QT
                        pose="looking-up"
                        height={54}
                        interactive={true}
                        motion="bob"
                        speechBubble="An empty library! Click 'New Quiz' to fill it up."
                        alt="QT looking up curiously"
                      />
                      <p className="text-[13px] text-slate-500 font-normal mt-2">
                        {loadError
                          ? 'The quiz library could not be loaded. Use Retry above.'
                          : 'No quizzes generated yet. Click "New Quiz" above to create one.'}
                      </p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {exportingQuiz && (
        <ExportQuizModal
          isOpen={!!exportingQuiz}
          onClose={() => setExportingQuiz(null)}
          quiz={exportingQuiz}
          onExport={async (fmt, opts) => {
            await downloadQuizFile(exportingQuiz, fmt, opts);
          }}
        />
      )}
    </div>
  );
}
