'use client';

import React, { useState } from 'react';
import {
  X,
  FileDown,
  Sparkles,
  Award,
  Type,
  Layers,
  Palette,
  Maximize2,
  Check,
  Copy,
  Sliders,
  Image as ImageIcon,
  CheckCircle2,
  Trash2,
  ExternalLink
} from 'lucide-react';
import { CustomPptTemplate } from '@/lib/types';
import { api } from '@/lib/api';

interface CustomTemplateViewModalProps {
  isOpen: boolean;
  onClose: () => void;
  template: CustomPptTemplate | null;
  onDelete?: (id: string, name: string) => void;
  onUseTemplate?: (template: CustomPptTemplate) => void;
}

type SlideTab = 'cover' | 'question' | 'divider' | 'answer';

export default function CustomTemplateViewModal({
  isOpen,
  onClose,
  template,
  onDelete,
  onUseTemplate,
}: CustomTemplateViewModalProps) {
  const [activeSlideTab, setActiveSlideTab] = useState<SlideTab>('cover');
  const [showWireframe, setShowWireframe] = useState(false);
  const [copiedColor, setCopiedColor] = useState<string | null>(null);
  const [imgErrors, setImgErrors] = useState<Record<string, boolean>>({});

  if (!isOpen || !template) return null;

  const isSchool = template.category === 'qshala_school';
  const geometry = template.layout_metadata?.layouts?.geometry;
  const questionBox = geometry?.question_box || { left: 1.0, top: 1.2, width: 11.33, height: 4.5 };
  const mediaBox = geometry?.media_box || { left: 6.8, top: 1.2, width: 5.5, height: 4.5 };

  const handleCopyColor = (colorHex: string) => {
    navigator.clipboard.writeText(colorHex);
    setCopiedColor(colorHex);
    setTimeout(() => setCopiedColor(null), 2000);
  };

  const activePreviewUrl = api.getTemplatePreviewUrl(template.id, activeSlideTab);
  const downloadUrl = api.getTemplateDownloadUrl(template.id);
  const colorScheme = template.layout_metadata?.theme?.color_scheme;
  const themeName = template.layout_metadata?.theme?.theme_name || template.layout_metadata?.theme?.name;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-3 sm:p-5 overflow-y-auto">
      <div
        className="w-full max-w-4xl rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden animate-in fade-in zoom-in-95 duration-150 my-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-5 sm:px-6 py-4 bg-slate-50/80">
          <div className="flex items-center gap-3 min-w-0">
            <div
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
                isSchool ? 'bg-amber-100 text-amber-700' : 'bg-orange-100 text-orange-700'
              }`}
            >
              {isSchool ? <Sparkles className="h-5 w-5" /> : <Award className="h-5 w-5" />}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-base sm:text-lg font-bold text-slate-900 truncate" title={template.name}>
                  {template.name}
                </h3>
                <span
                  className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1 ${
                    isSchool
                      ? 'bg-amber-100 text-amber-800 border border-amber-200'
                      : 'bg-orange-100 text-orange-800 border border-orange-200'
                  }`}
                >
                  {isSchool ? 'QShala School' : 'ZVQ Varsity'}
                </span>
                <span className="text-[10px] font-mono text-slate-500 bg-slate-200/70 px-1.5 py-0.5 rounded">
                  {template.aspect_ratio || '16:9'}
                </span>
              </div>
              <p className="text-xs text-slate-500 truncate mt-0.5">
                {template.description || 'Extracted custom PowerPoint template layout and typography.'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <a
              href={downloadUrl}
              download
              className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-100 transition shadow-sm"
              title="Download clean master presentation file (.pptx)"
            >
              <FileDown className="h-3.5 w-3.5 text-slate-600" />
              <span>Download Master (.pptx)</span>
            </a>
            <button
              onClick={onClose}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition"
              title="Close viewer"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Modal Body */}
        <div className="p-5 sm:p-6 space-y-6 max-h-[78vh] overflow-y-auto">
          {/* Slide Tab Controls & Wireframe Toggle */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/80 overflow-x-auto max-w-full">
              <button
                type="button"
                onClick={() => setActiveSlideTab('cover')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                  activeSlideTab === 'cover'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                Cover Slide
              </button>
              <button
                type="button"
                onClick={() => setActiveSlideTab('question')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                  activeSlideTab === 'question'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                Question Slide (28pt)
              </button>
              <button
                type="button"
                onClick={() => setActiveSlideTab('divider')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                  activeSlideTab === 'divider'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                Answer Divider Slide
              </button>
              <button
                type="button"
                onClick={() => setActiveSlideTab('answer')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                  activeSlideTab === 'answer'
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                Answer Reveal Slide
              </button>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setShowWireframe(!showWireframe)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition ${
                  showWireframe
                    ? 'bg-blue-50 border-blue-200 text-blue-700'
                    : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}
              >
                <Sliders className="h-3.5 w-3.5" />
                <span>{showWireframe ? 'Hide Safe Media Boxes' : 'Show Safe Media Boxes'}</span>
              </button>
            </div>
          </div>

          {/* Interactive Slide Canvas Stage */}
          <div className="relative aspect-[16/9] w-full rounded-2xl border border-slate-200 bg-slate-900 shadow-inner overflow-hidden flex flex-col justify-between p-4 sm:p-6 select-none">
            {/* Smooth Non-Glitching Extracted Slide Backgrounds */}
            {(['cover', 'question', 'divider', 'answer'] as const).map((tab) => {
              if (imgErrors[tab]) return null;
              const tabUrl = api.getTemplatePreviewUrl(template.id, tab);
              const isActive = activeSlideTab === tab;
              return (
                <img
                  key={`${template.id}-${tab}`}
                  src={tabUrl}
                  alt={`Template ${tab} Slide Background`}
                  onError={() => setImgErrors((prev) => ({ ...prev, [tab]: true }))}
                  className={`absolute inset-0 w-full h-full object-cover pointer-events-none transition-opacity duration-300 ease-in-out ${
                    isActive ? 'opacity-100 z-0' : 'opacity-0 -z-10 pointer-events-none'
                  }`}
                  loading="eager"
                />
              );
            })}

            {/* Subtle contrast framing (non-destructive; keeps extracted background artwork crisp) */}
            <div className="absolute inset-0 bg-gradient-to-t from-black/20 via-transparent to-black/10 pointer-events-none" />

            {/* Top Bar Branding */}
            <div className="relative z-10 flex items-center justify-between">
              <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-white/80 backdrop-blur-md border border-white/60 shadow-xs">
                <span
                  className="text-[11px] font-black tracking-widest uppercase"
                  style={{ color: template.header_color_hex || '#000000' }}
                >
                  {isSchool ? 'QSHALA SCHOOL INTELLIGENCE' : 'ZERODHA VARSITY QUIZ'}
                </span>
                <span className="text-[10px] bg-slate-200/80 text-slate-700 px-1.5 py-0.5 rounded font-mono">
                  {template.aspect_ratio || '16:9'}
                </span>
              </div>
              <span className="text-[11px] font-semibold text-slate-800 px-2.5 py-1 rounded-full bg-white/80 backdrop-blur-md border border-white/60 shadow-xs">
                {activeSlideTab === 'cover' && 'Slide 1 • Title Deck'}
                {activeSlideTab === 'question' && 'Slide 2 • Question & Media Safe Zone'}
                {activeSlideTab === 'divider' && 'Slide 3 • Answer Transition'}
                {activeSlideTab === 'answer' && 'Slide 4 • Answer Reveal'}
              </span>
            </div>

            {/* Center Stage: Interactive Slide Content Preview */}
            <div className="relative z-10 my-auto flex flex-col items-center justify-center text-center px-2 w-full">
              {activeSlideTab === 'cover' && (
                <div className="max-w-xl mx-auto space-y-3 p-5 sm:p-6 rounded-2xl bg-white/80 backdrop-blur-md border border-white/70 shadow-lg text-center">
                  <h1
                    className="font-extrabold tracking-tight leading-tight line-clamp-2"
                    style={{
                      fontFamily: template.title_font || 'inherit',
                      color: template.title_color_hex || '#1E293B',
                      fontSize: 'clamp(1.4rem, 3.2vw, 2.3rem)',
                    }}
                  >
                    Annual Championship 2026
                  </h1>
                  <p
                    className="text-slate-700 font-medium"
                    style={{
                      fontFamily: template.body_font || 'inherit',
                      fontSize: 'clamp(0.85rem, 1.5vw, 1.1rem)',
                    }}
                  >
                    {isSchool ? 'Grades 6 - 8 • Middle School Assembly Track' : 'Bengaluru Campus Tournament Finals'}
                  </p>
                  <div className="pt-1">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-900/10 text-slate-800 text-[11px] font-semibold border border-black/5">
                      <Sparkles className="h-3 w-3 text-amber-600" />
                      Title Font: <strong className="font-semibold">{template.title_font || 'Causten Round'}</strong>
                    </span>
                  </div>
                </div>
              )}

              {activeSlideTab === 'question' && (
                <div className="w-full flex flex-col sm:flex-row items-center justify-between gap-4 sm:gap-6 px-2 sm:px-4">
                  {/* Question Text Box */}
                  <div className="flex-1 text-left space-y-2.5 p-4 sm:p-5 rounded-2xl bg-white/80 backdrop-blur-md border border-white/70 shadow-lg">
                    <span
                      className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-amber-100 text-amber-900"
                      style={{ fontFamily: template.header_font || 'inherit' }}
                    >
                      Question 01
                    </span>
                    <p
                      className="font-medium text-slate-900 leading-snug"
                      style={{
                        fontFamily: template.body_font || 'inherit',
                        fontSize: 'clamp(0.95rem, 1.8vw, 1.3rem)',
                        color: template.body_color_hex || '#0F172A',
                      }}
                    >
                      Which celestial event inspired ancient maritime navigators and was famously depicted in the constellation maps?
                    </p>
                    <div className="pt-1 flex items-center gap-2 text-[10px] text-slate-600">
                      <span className="bg-amber-100 text-amber-900 font-bold px-2 py-0.5 rounded-full">
                        Uniform 28pt Locked Body Font
                      </span>
                      <span>•</span>
                      <span>Font: {template.body_font || 'Causten Round Regular'}</span>
                    </div>
                  </div>

                  {/* Media Safe Area Box */}
                  <div
                    className={`w-full sm:w-56 sm:h-40 rounded-2xl flex flex-col items-center justify-center p-3.5 transition backdrop-blur-md shadow-lg ${
                      showWireframe
                        ? 'border-2 border-dashed border-blue-500 bg-blue-50/85 text-blue-800'
                        : 'border border-white/70 bg-white/80 text-slate-600'
                    }`}
                  >
                    <ImageIcon className="h-7 w-7 mb-1 opacity-70" />
                    <span className="text-xs font-bold text-center">Safe Media Area</span>
                    <span className="text-[10px] text-center opacity-80 mt-0.5">
                      {mediaBox.width}&quot; &times; {mediaBox.height}&quot; Widescreen Box
                    </span>
                    {showWireframe && (
                      <span className="mt-1 text-[9px] font-mono text-blue-700 bg-blue-100 px-1.5 py-0.5 rounded">
                        L: {mediaBox.left}&quot;, T: {mediaBox.top}&quot;
                      </span>
                    )}
                  </div>
                </div>
              )}

              {activeSlideTab === 'divider' && (
                <div className="max-w-md mx-auto space-y-3 p-5 sm:p-6 rounded-2xl bg-white/80 backdrop-blur-md border border-white/70 shadow-lg text-center">
                  <div
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider bg-orange-100 text-orange-800 border border-orange-200"
                    style={{ fontFamily: template.header_font || 'inherit' }}
                  >
                    <Layers className="h-3.5 w-3.5" />
                    Section Transition
                  </div>
                  <h2
                    className="font-black text-slate-900 tracking-tight"
                    style={{
                      fontFamily: template.header_font || template.title_font || 'inherit',
                      fontSize: 'clamp(1.8rem, 3.8vw, 2.8rem)',
                      color: template.title_color_hex || '#0F172A',
                    }}
                  >
                    Answer
                  </h2>
                  <p className="text-xs text-slate-600">
                    Divider slide signaling transition to reveal & explanation
                  </p>
                </div>
              )}

              {activeSlideTab === 'answer' && (
                <div className="max-w-md mx-auto space-y-3 p-5 sm:p-6 rounded-2xl bg-white/80 backdrop-blur-md border border-white/70 shadow-lg text-center">
                  <div
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider bg-emerald-100 text-emerald-800 border border-emerald-200"
                    style={{ fontFamily: template.header_font || 'inherit' }}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Answer Reveal
                  </div>
                  <h2
                    className="font-black text-slate-900 tracking-tight"
                    style={{
                      fontFamily: template.title_font || 'inherit',
                      fontSize: 'clamp(1.3rem, 2.8vw, 2.0rem)',
                      color: template.title_color_hex || '#0F172A',
                    }}
                  >
                    The Polaris North Star
                  </h2>
                  <p
                    className="text-slate-700 leading-relaxed text-xs sm:text-sm"
                    style={{
                      fontFamily: template.body_font || 'inherit',
                    }}
                  >
                    Historically used by polar explorers and Mediterranean sailors to determine geographical latitude.
                  </p>
                </div>
              )}
            </div>

            {/* Bottom Slide Footer Bar */}
            <div className="relative z-10 flex items-center justify-between text-[11px] text-slate-700">
              <span className="font-mono px-2.5 py-0.5 rounded-full bg-white/80 backdrop-blur-md border border-white/60 shadow-xs">
                Slide Dimensions: {template.slide_width_in || 13.33}&quot; &times; {template.slide_height_in || 7.5}&quot;
              </span>
              <span className="font-semibold px-2.5 py-0.5 rounded-full bg-white/80 backdrop-blur-md border border-white/60 shadow-xs">
                {isSchool ? 'Official QShala Brand Engine' : 'Zerodha Varsity Master System'}
              </span>
            </div>
          </div>

          {/* Typography & Layout Metadata Inspector Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Title Typography Card */}
            <div className="rounded-xl border border-slate-200/90 bg-slate-50/60 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs font-bold text-slate-900 border-b border-slate-200/60 pb-1.5">
                <span className="flex items-center gap-1.5">
                  <Type className="h-3.5 w-3.5 text-slate-500" />
                  Title Typography
                </span>
                <span className="text-[11px] font-mono text-slate-500">
                  {template.title_size_pt || 44}pt
                </span>
              </div>
              <div className="text-sm font-semibold text-slate-800 line-clamp-1" title={template.title_font || 'System'}>
                {template.title_font || 'Causten Round'}
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">Color:</span>
                <button
                  type="button"
                  onClick={() => handleCopyColor(template.title_color_hex || '#1E293B')}
                  className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-slate-200 hover:bg-slate-50 font-mono text-[11px] cursor-pointer"
                  title="Click to copy color hex"
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full border border-black/10"
                    style={{ backgroundColor: template.title_color_hex || '#1E293B' }}
                  />
                  <span>{template.title_color_hex || '#1E293B'}</span>
                  {copiedColor === (template.title_color_hex || '#1E293B') ? (
                    <Check className="h-3 w-3 text-emerald-600" />
                  ) : (
                    <Copy className="h-3 w-3 text-slate-400" />
                  )}
                </button>
              </div>
            </div>

            {/* Body Typography Card */}
            <div className="rounded-xl border border-slate-200/90 bg-slate-50/60 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs font-bold text-slate-900 border-b border-slate-200/60 pb-1.5">
                <span className="flex items-center gap-1.5">
                  <Layers className="h-3.5 w-3.5 text-slate-500" />
                  Body Standard
                </span>
                <span className="text-[10px] font-bold uppercase bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
                  28pt Locked
                </span>
              </div>
              <div className="text-sm font-semibold text-slate-800 line-clamp-1" title={template.body_font || 'System'}>
                {template.body_font || 'Causten Round Regular'}
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">Color:</span>
                <button
                  type="button"
                  onClick={() => handleCopyColor(template.body_color_hex || '#0F172A')}
                  className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-slate-200 hover:bg-slate-50 font-mono text-[11px] cursor-pointer"
                  title="Click to copy color hex"
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full border border-black/10"
                    style={{ backgroundColor: template.body_color_hex || '#0F172A' }}
                  />
                  <span>{template.body_color_hex || '#0F172A'}</span>
                  {copiedColor === (template.body_color_hex || '#0F172A') ? (
                    <Check className="h-3 w-3 text-emerald-600" />
                  ) : (
                    <Copy className="h-3 w-3 text-slate-400" />
                  )}
                </button>
              </div>
            </div>

            {/* Dimensions & Master Deck Card */}
            <div className="rounded-xl border border-slate-200/90 bg-slate-50/60 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs font-bold text-slate-900 border-b border-slate-200/60 pb-1.5">
                <span className="flex items-center gap-1.5">
                  <Palette className="h-3.5 w-3.5 text-slate-500" />
                  Geometry & Master
                </span>
                <span className="text-[11px] font-mono text-slate-500">
                  {template.aspect_ratio || '16:9'}
                </span>
              </div>
              <div className="text-xs text-slate-600 space-y-1">
                <div className="flex justify-between">
                  <span>Dimensions:</span>
                  <strong className="font-semibold text-slate-800">
                    {template.slide_width_in || 13.33}&quot; &times; {template.slide_height_in || 7.5}&quot;
                  </strong>
                </div>
                <div className="flex justify-between">
                  <span>Master Storage:</span>
                  <span className="font-mono text-[11px] text-slate-700">
                    {template.file_size_bytes ? `${Math.round(template.file_size_bytes / 1024)} KB` : 'Verified PPTX'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Extracted Theme Color Palette */}
          {colorScheme && Object.keys(colorScheme).length > 0 && (
            <div className="rounded-xl border border-slate-200/90 bg-slate-50/60 p-4 space-y-3">
              <div className="flex items-center justify-between text-xs font-bold text-slate-900 border-b border-slate-200/60 pb-1.5">
                <span className="flex items-center gap-1.5">
                  <Palette className="h-3.5 w-3.5 text-slate-500" />
                  Extracted Slide Theme Palette {themeName ? `(${themeName})` : ''}
                </span>
                <span className="text-[11px] font-mono text-slate-500">
                  {Object.keys(colorScheme).length} Theme Swatches
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {Object.entries(colorScheme).map(([slot, hex]) => (
                  <button
                    key={slot}
                    type="button"
                    onClick={() => handleCopyColor(hex)}
                    className="flex items-center gap-2 p-1.5 rounded-lg bg-white border border-slate-200 hover:border-slate-300 hover:shadow-xs transition text-left group cursor-pointer"
                    title={`Click to copy ${slot}: ${hex}`}
                  >
                    <span
                      className="h-5 w-5 rounded-md border border-black/10 shrink-0 shadow-xs"
                      style={{ backgroundColor: hex }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider truncate">
                        {slot}
                      </div>
                      <div className="text-[11px] font-mono text-slate-700 truncate">
                        {hex}
                      </div>
                    </div>
                    {copiedColor === hex ? (
                      <Check className="h-3 w-3 text-emerald-600 shrink-0" />
                    ) : (
                      <Copy className="h-3 w-3 text-slate-300 group-hover:text-slate-500 shrink-0 transition" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/80 px-6 py-4">
          <div className="text-xs text-slate-500">
            Created: <span className="font-medium text-slate-700">{template.created_at ? new Date(template.created_at).toLocaleString() : 'Recently'}</span>
          </div>

          <div className="flex items-center gap-2.5">
            {onDelete && (
              <button
                type="button"
                onClick={() => {
                  onDelete(template.id, template.name);
                  onClose();
                }}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50 border border-transparent hover:border-rose-200 transition"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Delete</span>
              </button>
            )}

            <a
              href={downloadUrl}
              download
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl border border-slate-300 bg-white text-xs font-semibold text-slate-700 hover:bg-slate-50 transition shadow-sm"
            >
              <FileDown className="h-3.5 w-3.5 text-slate-600" />
              <span>Download Master (.pptx)</span>
            </a>

            {onUseTemplate && (
              <button
                type="button"
                onClick={() => {
                  onUseTemplate(template);
                  onClose();
                }}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-orange-600 text-xs font-semibold text-white shadow-md shadow-orange-500/20 hover:bg-orange-700 transition"
              >
                <Check className="h-3.5 w-3.5" />
                <span>Select for Export</span>
              </button>
            )}

            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-slate-600 hover:bg-slate-100 transition"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
