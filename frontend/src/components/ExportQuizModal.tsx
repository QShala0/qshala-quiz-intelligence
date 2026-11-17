'use client';

import React, { useState, useEffect } from 'react';
import { 
  X, 
  FileDown, 
  Loader2, 
  Sparkles, 
  Check, 
  Building2, 
  GraduationCap, 
  QrCode, 
  IndianRupee,
  Award,
  Layers,
  BookOpen
} from 'lucide-react';
import { Quiz } from '@/lib/types';
import { ExportOptions } from '@/lib/api';

interface ExportQuizModalProps {
  isOpen: boolean;
  onClose: () => void;
  quiz: Quiz;
  onExport: (format: 'pptx' | 'json' | 'csv', options?: ExportOptions) => Promise<void>;
}

function cleanInitialTitle(rawTitle: string | undefined, topic: string | undefined): string {
  if (!rawTitle || rawTitle.toLowerCase() === 'custom' || rawTitle.toLowerCase() === 'untitled quiz') {
    return topic ? `${topic} Quiz` : 'Bengaluru South Zone';
  }
  let t = rawTitle.trim();
  // Strip common prompt prefixes/suffixes
  t = t.replace(/^(?:give\s+me|generate|create|make|pull)\s+\d*\s*/i, '');
  t = t.replace(/\s*(?:and\s+make\s+a\s+quiz|and\s+create\s+a\s+quiz|make\s+a\s+quiz|create\s+a\s+quiz|quiz\s+please|for\s+school|without\s+images?|text\s+only).*$/i, '');
  t = t.replace(/^questions?\s+(?:on|about|for)\s+/i, '');
  t = t.trim();
  if (t && t.length > 2 && t.toLowerCase() !== rawTitle.trim().toLowerCase()) {
    return `${t.charAt(0).toUpperCase() + t.slice(1)} Quiz`;
  }
  return rawTitle.trim();
}

export default function ExportQuizModal({
  isOpen,
  onClose,
  quiz,
  onExport,
}: ExportQuizModalProps) {
  // Top-Level Template Category: 'qshala' (QShala School Template) or 'zsv' (Zerodha Varsity Quiz)
  const [templateCategory, setTemplateCategory] = useState<'qshala' | 'zsv'>('qshala');
  
  // ZSV Sub-Category: 'school' (Campus Edition) or 'corporate' (Corporate Master)
  const [zsvSubtype, setZsvSubtype] = useState<'school' | 'corporate'>('school');
  
  // QShala School Template Fields
  const [customSubtitle, setCustomSubtitle] = useState('Grades 6 - 8 • Middle School Assembly');
  const [showAnswerDivider, setShowAnswerDivider] = useState(true);

  // ZSV Fields
  const [campusEdition, setCampusEdition] = useState('Bangalore Campus Edition');
  const [prizeMoney, setPrizeMoney] = useState('3.5 lakh');
  const [includeSocialSlides, setIncludeSocialSlides] = useState(true);
  
  // Common Fields
  const [customTitle, setCustomTitle] = useState('');
  const [isExporting, setIsExporting] = useState(false);

  useEffect(() => {
    if (quiz) {
      setCustomTitle(cleanInitialTitle(quiz.title, quiz.topic));
    }
  }, [quiz]);

  if (!isOpen) return null;

  const handleDownload = async () => {
    try {
      setIsExporting(true);
      const effectiveQuizType = templateCategory === 'qshala' ? 'qshala_school' : zsvSubtype;
      await onExport('pptx', {
        quizType: effectiveQuizType,
        templateCategory,
        campusEdition: templateCategory === 'zsv' ? (campusEdition.trim() || undefined) : undefined,
        prizeMoney: templateCategory === 'zsv' && zsvSubtype === 'school' ? (prizeMoney.trim() || undefined) : undefined,
        customTitle: customTitle.trim() || undefined,
        customSubtitle: templateCategory === 'qshala' ? (customSubtitle.trim() || undefined) : undefined,
        includeSocialSlides: templateCategory === 'zsv' && zsvSubtype === 'school' ? includeSocialSlides : false,
        showAnswerDivider: templateCategory === 'qshala' ? showAnswerDivider : true,
      });
      onClose();
    } catch (err) {
      console.error('Failed to export presentation:', err);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div 
        className="w-full max-w-xl rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden animate-in fade-in zoom-in-95 duration-150 my-8"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4 bg-slate-50/70">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-100 text-orange-600">
              <FileDown className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-slate-900">Export Presentation (.pptx)</h3>
              <p className="text-xs text-slate-500">Select template brand, typography style, and slide configurations</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isExporting}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="space-y-5 px-6 py-5 max-h-[75vh] overflow-y-auto">
          {/* Top-Level Category Selector */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
              Template Brand & Category
            </label>
            <div className="grid grid-cols-2 gap-3">
              {/* Option 1: QShala School Template */}
              <button
                type="button"
                onClick={() => setTemplateCategory('qshala')}
                className={`flex flex-col items-start p-3.5 rounded-xl border text-left transition relative ${
                  templateCategory === 'qshala'
                    ? 'border-amber-500 bg-amber-50/60 shadow-sm ring-1 ring-amber-500/20'
                    : 'border-slate-200 hover:border-slate-300 bg-white'
                }`}
              >
                <div className="flex items-center justify-between w-full mb-1">
                  <span className="text-xs font-bold text-amber-900 uppercase tracking-wider flex items-center gap-1.5">
                    <Sparkles className="h-3.5 w-3.5 text-amber-600" />
                    QShala School
                  </span>
                  {templateCategory === 'qshala' && (
                    <div className="h-4 w-4 rounded-full bg-amber-600 text-white flex items-center justify-center">
                      <Check className="h-2.5 w-2.5" />
                    </div>
                  )}
                </div>
                <div className="text-sm font-semibold text-slate-900">QShala School Template</div>
                <div className="text-xs text-slate-500 mt-1 leading-snug">
                  Official QShala brand artwork, Causten Round font, 16:9 widescreen frame
                </div>
              </button>

              {/* Option 2: Zerodha Varsity Quiz (ZSV) */}
              <button
                type="button"
                onClick={() => setTemplateCategory('zsv')}
                className={`flex flex-col items-start p-3.5 rounded-xl border text-left transition relative ${
                  templateCategory === 'zsv'
                    ? 'border-orange-500 bg-orange-50/60 shadow-sm ring-1 ring-orange-500/20'
                    : 'border-slate-200 hover:border-slate-300 bg-white'
                }`}
              >
                <div className="flex items-center justify-between w-full mb-1">
                  <span className="text-xs font-bold text-orange-900 uppercase tracking-wider flex items-center gap-1.5">
                    <Award className="h-3.5 w-3.5 text-orange-600" />
                    Zerodha Varsity (ZSV)
                  </span>
                  {templateCategory === 'zsv' && (
                    <div className="h-4 w-4 rounded-full bg-orange-600 text-white flex items-center justify-center">
                      <Check className="h-2.5 w-2.5" />
                    </div>
                  )}
                </div>
                <div className="text-sm font-semibold text-slate-900">Zerodha Varsity Quiz</div>
                <div className="text-xs text-slate-500 mt-1 leading-snug">
                  Varsity tournament master decks, South Zone artwork & corporate tracks
                </div>
              </button>
            </div>
          </div>

          {/* Conditional Configuration Section */}
          {templateCategory === 'qshala' ? (
            /* QShala School Template Controls */
            <div className="space-y-4 rounded-xl border border-amber-200/80 bg-amber-50/20 p-4">
              <div className="flex items-center justify-between border-b border-amber-200/50 pb-2.5">
                <span className="text-xs font-bold uppercase tracking-wider text-amber-900 flex items-center gap-1.5">
                  <BookOpen className="h-4 w-4 text-amber-600" />
                  QShala School Style Configuration
                </span>
                <span className="text-[11px] font-semibold text-amber-700 bg-amber-100/70 px-2 py-0.5 rounded-full">
                  Causten Round • 44pt / 28pt
                </span>
              </div>

              {/* Presentation Title */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Deck Title (Slide 1)
                </label>
                <input
                  type="text"
                  value={customTitle}
                  onChange={(e) => setCustomTitle(e.target.value)}
                  placeholder="e.g., Annual Science & Nature Quiz 2026"
                  className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Rendered centrally in 44pt Bold Causten Round on the QShala signature 16:9 backdrop.
                </p>
              </div>

              {/* Subtitle / Grade Level */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Subtitle / Grade Level (Slide 1)
                </label>
                <input
                  type="text"
                  value={customSubtitle}
                  onChange={(e) => setCustomSubtitle(e.target.value)}
                  placeholder="e.g., Grades 6 - 8 • Middle School Assembly"
                  className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Centered 24pt subtitle text below main title.
                </p>
              </div>

              {/* Answer Divider Slide Toggle */}
              <div className="rounded-xl bg-white p-3.5 border border-slate-200">
                <label className="flex items-center gap-2.5 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={showAnswerDivider}
                    onChange={(e) => setShowAnswerDivider(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-amber-600 focus:ring-amber-500"
                  />
                  <div className="text-xs text-slate-700">
                    <span className="font-semibold text-slate-900 flex items-center gap-1.5">
                      <Layers className="h-3.5 w-3.5 text-amber-600" />
                      Include &quot;Answer&quot; Divider Slides
                    </span>
                    <span className="block text-slate-500 text-[11px] mt-0.5">
                      Inserts clean 44pt centered transition slide before each answer reveal.
                    </span>
                  </div>
                </label>
              </div>

              {/* QShala Style Guide Notice */}
              <div className="flex items-start gap-2 rounded-lg bg-amber-100/60 border border-amber-300/60 p-3 text-[11px] text-amber-900">
                <Sparkles className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
                <div>
                  <strong>QShala School Content Style Guide:</strong>
                  <ul className="list-disc list-inside mt-1 space-y-0.5 text-amber-800">
                    <li>Typography: 44pt bold title, 28pt centered question content in Causten Round.</li>
                    <li>Clean formatting: no bullet points, clues highlighted, centered high-res media.</li>
                    <li>Speaker notes: source links, media references, and presenter trivia context embedded.</li>
                  </ul>
                </div>
              </div>
            </div>
          ) : (
            /* Zerodha Varsity Quiz (ZSV) Controls */
            <div className="space-y-4 rounded-xl border border-orange-200/80 bg-orange-50/20 p-4">
              {/* Sub-Category Selector */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-2">
                  ZSV Edition & Sub-Category
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => setZsvSubtype('school')}
                    className={`flex items-center gap-2 p-2.5 rounded-lg border text-left transition ${
                      zsvSubtype === 'school'
                        ? 'border-orange-500 bg-white shadow-sm ring-1 ring-orange-500/20 text-orange-900 font-semibold'
                        : 'border-slate-200 hover:border-slate-300 bg-white/70 text-slate-700'
                    }`}
                  >
                    <GraduationCap className="h-4 w-4 text-orange-600 shrink-0" />
                    <div>
                      <div className="text-xs font-bold">School Quiz</div>
                      <div className="text-[10px] text-slate-500">Campus Edition & Prizes</div>
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setZsvSubtype('corporate')}
                    className={`flex items-center gap-2 p-2.5 rounded-lg border text-left transition ${
                      zsvSubtype === 'corporate'
                        ? 'border-blue-500 bg-white shadow-sm ring-1 ring-blue-500/20 text-blue-900 font-semibold'
                        : 'border-slate-200 hover:border-slate-300 bg-white/70 text-slate-700'
                    }`}
                  >
                    <Building2 className="h-4 w-4 text-blue-600 shrink-0" />
                    <div>
                      <div className="text-xs font-bold">Corporate Quiz</div>
                      <div className="text-[10px] text-slate-500">Dark Executive Theme</div>
                    </div>
                  </button>
                </div>
              </div>

              {zsvSubtype === 'school' ? (
                <>
                  {/* Campus Edition Input */}
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                      Campus Edition Name (Slide 1)
                    </label>
                    <input
                      type="text"
                      value={campusEdition}
                      onChange={(e) => setCampusEdition(e.target.value)}
                      placeholder="e.g., Bangalore Campus Edition, Mumbai Campus Edition"
                      className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                    />
                    <p className="mt-1 text-[11px] text-slate-500">
                      Rendered centrally on cover slide: <span className="font-semibold text-slate-700">— {campusEdition || 'Bangalore Campus Edition'} —</span>
                    </p>
                  </div>

                  {/* Prize Money Input */}
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                      Prize Money Amount (Slide 1)
                    </label>
                    <div className="relative">
                      <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                        <IndianRupee className="h-4 w-4 text-slate-500" />
                      </div>
                      <input
                        type="text"
                        value={prizeMoney}
                        onChange={(e) => setPrizeMoney(e.target.value)}
                        placeholder="e.g., 3.5 lakh, 50,000, 10,000"
                        className="w-full rounded-xl border border-slate-300 pl-9 pr-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                      />
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Dynamic cover prize callout: <span className="font-semibold text-slate-700">Win prizes worth ₹{prizeMoney || '3.5 lakh'}!</span>
                    </p>
                  </div>

                  {/* Social Slides Toggle */}
                  <div className="rounded-xl bg-white p-3.5 border border-slate-200">
                    <label className="flex items-center gap-2.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={includeSocialSlides}
                        onChange={(e) => setIncludeSocialSlides(e.target.checked)}
                        className="h-4 w-4 rounded border-slate-300 text-amber-500"
                      />
                      <div className="text-xs text-slate-700">
                        <span className="font-semibold text-slate-900 flex items-center gap-1.5">
                          <QrCode className="h-3.5 w-3.5 text-slate-500" />
                          Include Social Media & QR Code slides
                        </span>
                        <span className="block text-slate-500 text-[11px] mt-0.5">
                          Inserts Slide 2 (#varsityquiz) and Slide 3 (Varsity, Instagram, WhatsApp QR codes)
                        </span>
                      </div>
                    </label>
                  </div>
                </>
              ) : (
                <>
                  {/* Corporate Quiz Fields */}
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                      Deck Title (Slide 1)
                    </label>
                    <input
                      type="text"
                      value={customTitle}
                      onChange={(e) => setCustomTitle(e.target.value)}
                      placeholder="e.g., Corporate Finance Challenge 2026"
                      className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                      Cohort / Track Subtitle (Optional)
                    </label>
                    <input
                      type="text"
                      value={campusEdition}
                      onChange={(e) => setCampusEdition(e.target.value)}
                      placeholder="e.g., Executive Track, FinTech Edition"
                      className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 placeholder:text-slate-400 focus:outline-none bg-white"
                    />
                  </div>
                </>
              )}

              {/* ZSV Rhythm Notice */}
              <div className="flex items-center gap-2 rounded-lg bg-orange-100/60 border border-orange-300/60 px-3 py-2 text-[11px] text-orange-900">
                <Sparkles className="h-3.5 w-3.5 text-orange-600 shrink-0" />
                <span>
                  <strong>ZSV Tournament Rhythm:</strong> Question → &quot;Answer&quot; divider slide → Answer reveal slide.
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-6 py-4">
          <div className="text-xs text-slate-500">
            Selected: <span className="font-semibold text-slate-800">
              {templateCategory === 'qshala' 
                ? 'QShala School Template' 
                : (zsvSubtype === 'school' ? 'Zerodha Varsity School Quiz' : 'Zerodha Varsity Corporate Quiz')}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={isExporting}
              className="rounded-xl px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-200/60 transition"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDownload}
              disabled={isExporting}
              className="flex items-center gap-2 rounded-xl bg-orange-600 px-5 py-2.5 text-xs font-semibold text-white shadow-md shadow-orange-500/20 hover:bg-orange-700 transition disabled:opacity-50"
            >
              {isExporting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Generating PPTX...
                </>
              ) : (
                <>
                  <FileDown className="h-4 w-4" />
                  Download Presentation (.pptx)
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
