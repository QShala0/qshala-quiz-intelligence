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
import { QT } from '../components/ui';
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
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-3 border-b-2 border-sun">
        <div className="flex items-center gap-3">
          <h1 className="text-[28px] sm:text-[32px] font-black tracking-tight text-ink leading-tight">
            Dashboard Overview
          </h1>
          <div className="hidden sm:inline-block">
            <QT
              pose="round"
              height={44}
              interactive={true}
              motion="breathe"
              speechBubble={[
                'Welcome to QShala Quiz Intelligence!',
                'Tip: Pair 5 questions for a quick classroom warmup.',
                'Curiosity is the engine of learning!',
              ]}
              bubblePosition="top-right"
              alt="Round QT companion"
            />
          </div>
        </div>

        {/* Top Direct Actions */}
        <div className="flex items-center gap-3 shrink-0">
          <Link
            href="/uploads"
            className="inline-flex items-center gap-2 rounded-xl border-2 border-ink/15 bg-white px-3.5 py-2 text-[13.5px] font-bold text-ink shadow-xs hover:bg-slate-50 transition-colors"
          >
            <UploadCloud className="h-4 w-4 text-ink/70" />
            <span>Upload Materials</span>
          </Link>

          <Link
            href="/generate"
            className="inline-flex items-center gap-2 rounded-xl bg-sun px-4 py-2 text-[13.5px] font-bold text-ink shadow-sticker-sm hover:brightness-105 active:scale-[0.99] transition-all"
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
          <div className="rounded-[20px] border border-sun/30 bg-white shadow-sticker-sm overflow-hidden">
            <div className="p-5 border-b border-slate-100">
              <div className="flex items-center justify-between">
                <h3 className="section-bar text-[13px] tracking-wider">
                  Recent Generated Quizzes
                </h3>
                <Link
                  href="/quizzes"
                  className="flex items-center gap-1 text-[13px] font-bold text-ink hover:text-sky-700 transition-colors shrink-0 ml-4"
                >
                  <span>View all quizzes</span>
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
              <p className="text-[12.5px] text-slate-500 font-normal mt-1.5">
                Slide-pair quizzes calibrated for school and tournament audiences
              </p>
            </div>

            <div className="divide-y divide-slate-100">
              {stats?.recent_quizzes && stats.recent_quizzes.length > 0 ? (
                stats.recent_quizzes.slice(0, 5).map((quiz) => (
                  <Link
                    key={quiz.id}
                    href={`/quizzes/${quiz.id}`}
                    className="flex flex-col sm:flex-row sm:items-center justify-between p-4 sm:px-5 hover:bg-amber-50/40 transition-colors group gap-3"
                  >
                    <div className="flex items-start sm:items-center gap-3.5 min-w-0">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sun/20 text-ink group-hover:bg-sun group-hover:text-ink transition-colors">
                        <Sparkles className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[14px] font-bold text-ink group-hover:text-ink transition-colors truncate">
                          {quiz.title}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 mt-1 text-[12px] text-slate-500">
                          <span className="font-semibold text-slate-700">{quiz.topic}</span>
                          <span className="text-slate-300">•</span>
                          <span>{quiz.question_count} Questions</span>
                          <span className="text-slate-300">•</span>
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-700">
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
                      <div className="flex items-center gap-1 text-[12px] font-bold text-ink opacity-0 group-hover:opacity-100 transition-opacity">
                        <span>View</span>
                        <ArrowRight className="h-3 w-3" />
                      </div>
                    </div>
                  </Link>
                ))
              ) : (
                <div className="p-8 text-center flex flex-col items-center">
                  <QT
                    pose="box-yellow"
                    height={48}
                    motion="bob"
                    interactive={true}
                    speechBubble="If it fits, I sits! Make a quiz to fill this space."
                    alt="QT in a yellow box"
                  />
                  <p className="text-[13px] text-slate-500 font-normal mt-2">No quizzes generated yet.</p>
                  <Link
                    href="/generate"
                    className="mt-2 inline-flex items-center gap-1.5 text-[13px] font-bold text-ink hover:underline"
                  >
                    <span>Create your first quiz</span>
                    <ArrowRight className="h-3 w-3" />
                  </Link>
                </div>
              )}
            </div>
          </div>

          {/* Recent Uploaded Quizzes */}
          <div className="rounded-[20px] border border-sun/30 bg-white shadow-sticker-sm overflow-hidden">
            <div className="p-5 border-b border-slate-100">
              <div className="flex items-center justify-between">
                <h3 className="section-bar text-[13px] tracking-wider">
                  Uploaded Quizzes
                </h3>
                <Link
                  href="/uploads"
                  className="flex items-center gap-1 text-[13px] font-bold text-ink hover:text-sky-700 transition-colors shrink-0 ml-4"
                >
                  <span>View all uploaded quizzes</span>
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
              <p className="text-[12.5px] text-slate-500 font-normal mt-1.5">
                Uploaded PowerPoint presentations and quiz decks
              </p>
            </div>

            <div className="divide-y divide-slate-100">
              {stats?.recent_documents && stats.recent_documents.length > 0 ? (
                stats.recent_documents.slice(0, 4).map((doc) => (
                  <div key={doc.id} className="flex items-center justify-between p-4 sm:px-5 hover:bg-slate-50/50 transition-colors">
                    <div className="flex items-center gap-3.5 min-w-0">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-700">
                        <FileText className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-[13.5px] font-bold text-ink">
                          {doc.title}
                        </p>
                        <p className="text-[12px] text-slate-500 font-normal mt-0.5">
                          {doc.slide_count} slides • {doc.question_count} questions extracted
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5 shrink-0">
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-bold text-emerald-800 border border-emerald-200">
                        <CheckCircle2 className="h-3 w-3 text-emerald-700" />
                        Completed
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="p-8 text-center flex flex-col items-center">
                  <QT
                    pose="fishbowl"
                    height={40}
                    interactive={true}
                    speechBubble="Waiting for your PPTX decks to dive in!"
                    alt="QT looking at fishbowl"
                  />
                  <p className="text-[13px] text-slate-400 mt-2">No quizzes uploaded yet.</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column (4 cols): Knowledge Distribution & Workflow */}
        <div className="lg:col-span-4 space-y-6">
          {/* Topic Coverage & Knowledge Distribution */}
          <div className="rounded-[20px] border border-sun/30 bg-white p-5 shadow-sticker-sm">
            <div className="border-b border-slate-100 pb-3 mb-4 flex items-center justify-between">
              <div>
                <h3 className="section-bar text-[13px] tracking-wider">
                  Topic Coverage
                </h3>
                <p className="text-[12px] text-slate-500 font-normal mt-1">
                  Question distribution by topic
                </p>
              </div>
              <Link
                href="/knowledge-base"
                className="text-[12px] font-bold text-ink hover:text-sky-700 shrink-0 ml-3"
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
                        <span className="font-semibold text-ink">{t.topic}</span>
                        <span className="font-bold text-slate-600">{t.count} Qs</span>
                      </div>
                      <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-sky transition-all duration-500"
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
          <div className="rounded-[20px] border border-sun/30 bg-white p-5 shadow-sticker-sm space-y-4">
            <h3 className="section-bar text-[13px] tracking-wider">
              Platform Workflow
            </h3>
            <div className="space-y-3 text-[12.5px] text-slate-600">
              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sun text-ink font-black text-[11px] mt-0.5 shadow-xs">
                  1
                </div>
                <div>
                  <span className="font-bold text-ink">Upload Presentation Decks</span>
                  <p className="text-slate-500 font-normal">Add PPTX or PDF slides to expand the question repository.</p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sun text-ink font-black text-[11px] mt-0.5 shadow-xs">
                  2
                </div>
                <div>
                  <span className="font-bold text-ink">Automatic Extraction</span>
                  <p className="text-slate-500 font-normal">Slides are paired into Question → Answer & Explanation.</p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sun text-ink font-black text-[11px] mt-0.5 shadow-xs">
                  3
                </div>
                <div>
                  <span className="font-bold text-ink">Create Calibrated Quizzes</span>
                  <p className="text-slate-500 font-normal">Select target audience and difficulty distribution.</p>
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-slate-100">
              <Link
                href="/generate"
                className="flex items-center justify-center gap-1.5 w-full rounded-xl bg-sun py-2 text-[13px] font-bold text-ink shadow-sticker-sm hover:brightness-105 active:scale-[0.99] transition-all"
              >
                <span>Go to Create Quiz</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>

          {/* Need to add new material? Callout card */}
          <div className="rounded-[20px] border border-sun/40 bg-amber-50/60 p-5 shadow-sticker-sm">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-sun text-ink font-bold shadow-xs">
                <Lightbulb className="h-4 w-4" />
              </div>
              <div>
                <h4 className="text-[14px] font-bold text-ink">Need to add new material?</h4>
                <p className="mt-1 text-[13px] text-slate-600 leading-relaxed font-normal">
                  Upload your PPT, PPTX or PDF files and they'll be parsed into slide-pair questions
                  automatically.
                </p>
                <Link
                  href="/uploads"
                  className="mt-3 inline-flex items-center gap-1 text-[13px] font-bold text-ink hover:text-sky-700 transition-colors"
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
