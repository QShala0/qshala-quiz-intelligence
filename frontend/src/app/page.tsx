'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  FileText,
  Sparkles,
  ArrowRight,
  UploadCloud,
  Layers,
  Lightbulb,
  CheckCircle2,
  BookOpen,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';
import StatsOverview from '../components/StatsOverview';
import { api } from '../lib/api';
import { DashboardStats } from '../lib/types';

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getStats();
      setStats(data);
    } catch (err) {
      console.error('Error fetching dashboard stats:', err);
      const message =
        err instanceof Error && err.message
          ? err.message
          : 'Unable to reach the backend database.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void Promise.resolve().then(fetchStats);
  }, []);

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-7xl mx-auto space-y-8">
      {/* Header with Executive Title and Quick Navigation Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-slate-100">
        <div>
          <h1 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-slate-900 leading-tight">
            Dashboard Overview
          </h1>
        </div>

        {/* Top Direct Actions */}
        <div className="flex items-center gap-3 shrink-0">
          <Link
            href="/uploads"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-[13.5px] font-medium text-slate-700 shadow-xs hover:bg-slate-50 hover:text-slate-900 transition-colors"
          >
            <UploadCloud className="h-4 w-4 text-slate-500" />
            <span>Upload Materials</span>
          </Link>

          <Link
            href="/generate"
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-[13.5px] font-semibold text-white shadow-sm shadow-blue-500/20 hover:bg-blue-700 active:scale-[0.99] transition-all"
          >
            <Sparkles className="h-4 w-4" />
            <span>Create Quiz</span>
          </Link>
        </div>
      </div>

      {/* Error Alert Banner if live backend stats failed */}
      {error && !loading && (
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-3 text-[13.5px] text-amber-900">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
            <div>
              <span className="font-semibold">Unable to load live repository stats:</span>{' '}
              <span className="text-amber-800">{error}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={fetchStats}
            className="inline-flex items-center gap-1.5 self-start sm:self-auto rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-amber-900 hover:bg-amber-100/70 transition-colors cursor-pointer"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      )}

      {/* 4 Primary Statistics Cards */}
      <StatsOverview stats={stats} loading={loading} />

      {/* Main Grid: Operational Overview */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-7 items-start">
        {/* Left Column (8 cols): Recent Generated Quizzes & Knowledge Decks */}
        <div className="lg:col-span-8 space-y-7">
          {/* Recent Generated Quizzes */}
          <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
            <div className="flex items-center justify-between p-5 border-b border-slate-100">
              <div>
                <h3 className="text-[16px] font-bold tracking-tight text-slate-900">
                  Recent Generated Quizzes
                </h3>
                <p className="text-[12.5px] text-slate-500 font-normal mt-0.5">
                  Slide-pair quizzes calibrated for school and tournament audiences
                </p>
              </div>
              <Link
                href="/quizzes"
                className="flex items-center gap-1 text-[13px] font-semibold text-blue-600 hover:text-blue-700 transition-colors"
              >
                <span>View all quizzes</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="divide-y divide-slate-100">
              {stats?.recent_quizzes && stats.recent_quizzes.length > 0 ? (
                stats.recent_quizzes.slice(0, 5).map((quiz) => (
                  <Link
                    key={quiz.id}
                    href={`/quizzes/${quiz.id}`}
                    className="flex flex-col sm:flex-row sm:items-center justify-between p-4 sm:px-5 hover:bg-slate-50/70 transition-colors group gap-3"
                  >
                    <div className="flex items-start sm:items-center gap-3.5 min-w-0">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 group-hover:bg-blue-600 group-hover:text-white transition-colors">
                        <Sparkles className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[14px] font-semibold text-slate-900 group-hover:text-blue-600 transition-colors truncate">
                          {quiz.title}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 mt-1 text-[12px] text-slate-500">
                          <span className="font-medium text-slate-600">{quiz.topic}</span>
                          <span className="text-slate-300">•</span>
                          <span>{quiz.question_count} Questions</span>
                          <span className="text-slate-300">•</span>
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">
                            {quiz.difficulty}
                          </span>
                          {quiz.grade_range && (
                            <>
                              <span className="text-slate-300">•</span>
                              <span className="text-slate-500">{quiz.grade_range}</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 self-end sm:self-auto">
                      <span className="text-[12px] text-slate-400 font-normal">
                        {new Date(quiz.created_at).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                        })}
                      </span>
                      <div className="flex items-center gap-1 text-[12px] font-semibold text-blue-600 opacity-0 group-hover:opacity-100 transition-opacity">
                        <span>View</span>
                        <ArrowRight className="h-3 w-3" />
                      </div>
                    </div>
                  </Link>
                ))
              ) : (
                <div className="p-8 text-center">
                  <p className="text-[13px] text-slate-500 font-normal">No quizzes generated yet.</p>
                  <Link
                    href="/generate"
                    className="mt-2 inline-flex items-center gap-1.5 text-[13px] font-semibold text-blue-600 hover:underline"
                  >
                    <span>Create your first quiz</span>
                    <ArrowRight className="h-3 w-3" />
                  </Link>
                </div>
              )}
            </div>
          </div>

          {/* Recent Uploaded Quizzes */}
          <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
            <div className="flex items-center justify-between p-5 border-b border-slate-100">
              <div>
                <h3 className="text-[16px] font-bold tracking-tight text-slate-900">
                  Uploaded Quizzes
                </h3>
                <p className="text-[12.5px] text-slate-500 font-normal mt-0.5">
                  Uploaded PowerPoint presentations and quiz decks
                </p>
              </div>
              <Link
                href="/uploads"
                className="flex items-center gap-1 text-[13px] font-semibold text-blue-600 hover:text-blue-700 transition-colors"
              >
                <span>View all uploaded quizzes</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="divide-y divide-slate-100">
              {stats?.recent_documents && stats.recent_documents.length > 0 ? (
                stats.recent_documents.slice(0, 4).map((doc) => (
                  <div key={doc.id} className="flex items-center justify-between p-4 sm:px-5 hover:bg-slate-50/50 transition-colors">
                    <div className="flex items-center gap-3.5 min-w-0">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                        <FileText className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-[13.5px] font-semibold text-slate-900">
                          {doc.title}
                        </p>
                        <p className="text-[12px] text-slate-500 font-normal mt-0.5">
                          {doc.slide_count} slides • {doc.question_count} questions extracted
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5 shrink-0">
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-medium text-emerald-700 border border-emerald-100">
                        <CheckCircle2 className="h-3 w-3" />
                        Completed
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="p-8 text-center text-[13px] text-slate-400">
                  No quizzes uploaded yet.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column (4 cols): Knowledge Distribution & Workflow */}
        <div className="lg:col-span-4 space-y-6">
          {/* Topic Coverage & Knowledge Distribution */}
          <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50">
            <div className="border-b border-slate-100 pb-3 mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-[15px] font-bold tracking-tight text-slate-900">
                  Topic Coverage
                </h3>
                <p className="text-[12px] text-slate-500 font-normal">
                  Question distribution by topic
                </p>
              </div>
              <Link
                href="/knowledge-base"
                className="text-[12px] font-medium text-blue-600 hover:text-blue-700"
              >
                Browse questions
              </Link>
            </div>

            <div className="space-y-3.5">
              {stats?.top_topics && stats.top_topics.length > 0 ? (
                stats.top_topics.map((t) => {
                  const maxCount = Math.max(...stats.top_topics.map((item) => item.count), 1);
                  const pct = Math.round((t.count / maxCount) * 100);
                  return (
                    <div key={t.topic} className="space-y-1.5">
                      <div className="flex items-center justify-between text-[13px]">
                        <span className="font-medium text-slate-800">{t.topic}</span>
                        <span className="font-semibold text-slate-600">{t.count} Qs</span>
                      </div>
                      <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-blue-600 transition-all duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="py-3 text-center text-[12px] text-slate-400">
                  Upload decks to see topic breakdown.
                </div>
              )}
            </div>
          </div>

          {/* Platform Workflow Card */}
          <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50 space-y-3">
            <h3 className="text-[14px] font-bold text-slate-900 tracking-tight">
              Platform Workflow
            </h3>
            <div className="space-y-2.5 text-[12.5px] text-slate-600">
              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-600 font-bold text-[11px] mt-0.5">
                  1
                </div>
                <div>
                  <span className="font-semibold text-slate-800">Upload Presentation Decks</span>
                  <p className="text-slate-500 font-normal">Add PPTX or PDF slides to expand the question repository.</p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-600 font-bold text-[11px] mt-0.5">
                  2
                </div>
                <div>
                  <span className="font-semibold text-slate-800">Automatic Extraction</span>
                  <p className="text-slate-500 font-normal">Slides are paired into Question → Answer & Explanation.</p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-600 font-bold text-[11px] mt-0.5">
                  3
                </div>
                <div>
                  <span className="font-semibold text-slate-800">Create Calibrated Quizzes</span>
                  <p className="text-slate-500 font-normal">Select target audience and difficulty distribution.</p>
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-slate-100">
              <Link
                href="/generate"
                className="flex items-center justify-center gap-1.5 w-full rounded-lg bg-slate-900 py-2 text-[13px] font-semibold text-white hover:bg-slate-800 transition-colors"
              >
                <span>Go to Create Quiz</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>

          {/* Need to add new material? Callout card */}
          <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-100 text-blue-600">
                <Lightbulb className="h-4 w-4" />
              </div>
              <div>
                <h4 className="text-[14px] font-semibold text-slate-900">Need to add new material?</h4>
                <p className="mt-1 text-[13px] text-slate-600 leading-relaxed font-normal">
                  Upload your PPT, PPTX or PDF files and they'll be parsed into slide-pair questions
                  automatically.
                </p>
                <Link
                  href="/uploads"
                  className="mt-3 inline-flex items-center gap-1 text-[13px] font-semibold text-blue-600 hover:text-blue-700 transition-colors"
                >
                  <span>Go to Uploads</span>
                  <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
