'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  FileText,
  Upload,
  Trash2,
  Sparkles,
  Award,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Plus,
  Layers,
  Type,
  Maximize2,
  Eye
} from 'lucide-react';
import { api } from '@/lib/api';
import { CustomPptTemplate } from '@/lib/types';
import CustomTemplateViewModal from './CustomTemplateViewModal';

interface CustomTemplateManagerProps {
  onTemplateCreated?: (template: CustomPptTemplate) => void;
  defaultCategory?: 'qshala_school' | 'zvq';
}

export default function CustomTemplateManager({
  onTemplateCreated,
  defaultCategory,
}: CustomTemplateManagerProps) {
  const [templates, setTemplates] = useState<CustomPptTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Filter category
  const [filterCategory, setFilterCategory] = useState<'all' | 'qshala_school' | 'zvq'>(
    defaultCategory || 'all'
  );

  // Upload/Extract modal state
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [tplName, setTplName] = useState('');
  const [tplCategory, setTplCategory] = useState<'qshala_school' | 'zvq'>(
    defaultCategory || 'qshala_school'
  );
  const [tplDesc, setTplDesc] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Template Viewer Modal state
  const [selectedViewTemplate, setSelectedViewTemplate] = useState<CustomPptTemplate | null>(null);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listTemplates();
      setTemplates(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load custom templates');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTemplates();
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      if (!tplName) {
        // Auto-fill template name from filename without extension
        const cleanName = file.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ');
        setTplName(cleanName);
      }
    }
  };

  const handleExtract = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setError('Please choose a sample PowerPoint (.pptx) file');
      return;
    }
    if (!tplName.trim()) {
      setError('Please enter a template name');
      return;
    }

    try {
      setExtracting(true);
      setError(null);
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('name', tplName.trim());
      formData.append('category', tplCategory);
      if (tplDesc.trim()) {
        formData.append('description', tplDesc.trim());
      }

      const created = await api.extractTemplate(formData);
      setSuccessMsg(`Extracted template “${created.name}” successfully!`);
      setShowUploadModal(false);
      setSelectedFile(null);
      setTplName('');
      setTplDesc('');
      await loadTemplates();
      // Automatically open the template viewer to inspect the extracted template
      setSelectedViewTemplate(created);
      if (onTemplateCreated) {
        onTemplateCreated(created);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Template extraction failed');
    } finally {
      setExtracting(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete template "${name}"?`)) return;
    try {
      setError(null);
      await api.deleteTemplate(id);
      setSuccessMsg(`Template "${name}" deleted.`);
      await loadTemplates();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete template');
    }
  };

  const filteredTemplates = templates.filter((t) => {
    if (filterCategory === 'all') return true;
    return t.category === filterCategory;
  });

  return (
    <div className="space-y-6">
      {/* Top Bar with Filters and Upload CTA */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-white p-4 rounded-2xl border border-slate-200/80 shadow-sm">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setFilterCategory('all')}
            className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition ${
              filterCategory === 'all'
                ? 'bg-slate-900 text-white shadow-sm'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            All Templates ({templates.length})
          </button>
          <button
            onClick={() => setFilterCategory('qshala_school')}
            className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition flex items-center gap-1.5 ${
              filterCategory === 'qshala_school'
                ? 'bg-amber-600 text-white shadow-sm'
                : 'text-slate-600 hover:bg-amber-50 hover:text-amber-700'
            }`}
          >
            <Sparkles className="h-3 w-3" />
            QShala School ({templates.filter((t) => t.category === 'qshala_school').length})
          </button>
          <button
            onClick={() => setFilterCategory('zvq')}
            className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition flex items-center gap-1.5 ${
              filterCategory === 'zvq'
                ? 'bg-orange-600 text-white shadow-sm'
                : 'text-slate-600 hover:bg-orange-50 hover:text-orange-700'
            }`}
          >
            <Award className="h-3 w-3" />
            Zerodha Varsity (ZVQ) ({templates.filter((t) => t.category === 'zvq').length})
          </button>
        </div>

        <button
          onClick={() => {
            setShowUploadModal(true);
            setError(null);
            setSuccessMsg(null);
          }}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-orange-600 text-white text-xs font-bold hover:bg-orange-700 transition shadow-sm"
        >
          <Plus className="h-4 w-4" />
          Extract from Sample PPT
        </button>
      </div>

      {/* Notifications */}
      {error && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-xs">
          <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
          <span>{error}</span>
        </div>
      )}
      {successMsg && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Templates Grid */}
      {loading ? (
        <div className="flex flex-col items-center justify-center p-12 bg-white rounded-2xl border border-slate-200">
          <Loader2 className="h-6 w-6 animate-spin text-orange-600 mb-2" />
          <p className="text-xs text-slate-500 font-medium">Loading templates...</p>
        </div>
      ) : filteredTemplates.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 bg-white rounded-2xl border border-dashed border-slate-200 text-center">
          <div className="h-12 w-12 rounded-2xl bg-slate-100 flex items-center justify-center text-slate-400 mb-3">
            <FileText className="h-6 w-6" />
          </div>
          <h4 className="text-sm font-bold text-slate-800">No Custom Templates Found</h4>
          <p className="text-xs text-slate-500 max-w-sm mt-1">
            Upload any sample PowerPoint deck (.pptx) to extract its title layout, background slide, fonts, and uniform 28pt body typography.
          </p>
          <button
            onClick={() => setShowUploadModal(true)}
            className="mt-4 flex items-center gap-2 px-3.5 py-2 rounded-xl bg-slate-900 text-white text-xs font-semibold hover:bg-slate-800 transition shadow-sm"
          >
            <Upload className="h-3.5 w-3.5" />
            Upload Sample PPT
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredTemplates.map((tpl) => {
            const isSchool = tpl.category === 'qshala_school';
            return (
              <div
                key={tpl.id}
                className="flex flex-col justify-between p-4 rounded-2xl bg-white border border-slate-200/90 hover:border-slate-300 shadow-sm transition hover:shadow-md"
              >
                <div>
                  <div className="flex items-center justify-between mb-2.5">
                    <span
                      className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full flex items-center gap-1 ${
                        isSchool
                          ? 'bg-amber-100 text-amber-800 border border-amber-200'
                          : 'bg-orange-100 text-orange-800 border border-orange-200'
                      }`}
                    >
                      {isSchool ? <Sparkles className="h-2.5 w-2.5" /> : <Award className="h-2.5 w-2.5" />}
                      {isSchool ? 'QShala School' : 'ZVQ Varsity'}
                    </span>
                    <span className="text-[10px] text-slate-400 font-medium">
                      {tpl.aspect_ratio || '16:9'} Widescreen
                    </span>
                  </div>

                  <h4 className="text-sm font-bold text-slate-900 line-clamp-1">{tpl.name}</h4>
                  {tpl.description && (
                    <p className="text-xs text-slate-500 line-clamp-2 mt-1 leading-snug">
                      {tpl.description}
                    </p>
                  )}

                  <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 text-xs border border-slate-100">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 flex items-center gap-1.5">
                        <Type className="h-3 w-3 text-slate-400" />
                        Title Font:
                      </span>
                      <span className="font-semibold text-slate-800 line-clamp-1 max-w-[150px]">
                        {tpl.title_font || 'Causten Round'}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 flex items-center gap-1.5">
                        <Layers className="h-3 w-3 text-slate-400" />
                        Body Typography:
                      </span>
                      <span className="font-semibold text-slate-800">
                        {tpl.body_font || 'Causten Round'} • {tpl.body_size_pt || 28}pt
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-4 mt-4 border-t border-slate-100">
                  <span className="text-[11px] text-slate-400">
                    {tpl.created_at ? new Date(tpl.created_at).toLocaleDateString() : 'Extracted'}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setSelectedViewTemplate(tpl)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-xl transition cursor-pointer"
                      title="View template layout, fonts, and safe media boxes"
                    >
                      <Eye className="h-3.5 w-3.5 text-slate-600" />
                      <span>View</span>
                    </button>
                    <button
                      onClick={() => handleDelete(tpl.id, tpl.name)}
                      className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition cursor-pointer"
                      title="Delete template"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Upload & Extract Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden my-8 animate-in fade-in zoom-in-95">
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4 bg-slate-50/70">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-100 text-orange-600">
                  <Upload className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-slate-900">Extract Template from Sample PPT</h3>
                  <p className="text-xs text-slate-500">Upload a PowerPoint deck (.pptx) to extract its style</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowUploadModal(false)}
                disabled={extracting}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 transition"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleExtract} className="p-6 space-y-4">
              {/* File Dropzone */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Sample PowerPoint File (.pptx)
                </label>
                <div
                  onClick={() => fileInputRef.current?.click()}
                  className="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 hover:border-orange-500 rounded-2xl p-6 cursor-pointer bg-slate-50/50 hover:bg-orange-50/30 transition text-center"
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    onChange={handleFileChange}
                    className="hidden"
                  />
                  <div className="h-10 w-10 rounded-xl bg-orange-100 text-orange-600 flex items-center justify-center mb-2">
                    <FileText className="h-5 w-5" />
                  </div>
                  {selectedFile ? (
                    <div className="text-xs">
                      <span className="font-bold text-slate-900 block">{selectedFile.name}</span>
                      <span className="text-slate-500">({(selectedFile.size / (1024 * 1024)).toFixed(1)} MB)</span>
                    </div>
                  ) : (
                    <div className="text-xs text-slate-500">
                      <span className="font-bold text-slate-800 block">Click to browse or drop sample .pptx</span>
                      <span>Decks with title, question, and answer slides extract best</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Template Name */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Template Name *
                </label>
                <input
                  type="text"
                  required
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  placeholder="e.g., Kumarans School Annual Quiz Theme"
                  className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-orange-500/20"
                />
              </div>

              {/* Category Radio Group */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Template Category *
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => setTplCategory('qshala_school')}
                    className={`p-3 rounded-xl border text-left flex flex-col transition ${
                      tplCategory === 'qshala_school'
                        ? 'border-amber-500 bg-amber-50/60 ring-1 ring-amber-500/30'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <span className="text-xs font-bold text-amber-900 flex items-center gap-1.5 mb-1">
                      <Sparkles className="h-3.5 w-3.5 text-amber-600" />
                      QShala School
                    </span>
                    <span className="text-[11px] text-slate-500 leading-tight">
                      Curriculum, middle/high school assembly, school championships
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setTplCategory('zvq')}
                    className={`p-3 rounded-xl border text-left flex flex-col transition ${
                      tplCategory === 'zvq'
                        ? 'border-orange-500 bg-orange-50/60 ring-1 ring-orange-500/30'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <span className="text-xs font-bold text-orange-900 flex items-center gap-1.5 mb-1">
                      <Award className="h-3.5 w-3.5 text-orange-600" />
                      Zerodha Varsity (ZVQ)
                    </span>
                    <span className="text-[11px] text-slate-500 leading-tight">
                      Varsity quizzes, preliminary tournaments, corporate sets
                    </span>
                  </button>
                </div>
              </div>

              {/* Description */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-slate-700 mb-1.5">
                  Description / Notes (Optional)
                </label>
                <textarea
                  rows={2}
                  value={tplDesc}
                  onChange={(e) => setTplDesc(e.target.value)}
                  placeholder="Optional context about fonts or event edition..."
                  className="w-full rounded-xl border border-slate-300 px-3.5 py-2 text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-orange-500/20"
                />
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowUploadModal(false)}
                  disabled={extracting}
                  className="px-4 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-slate-600 hover:bg-slate-50 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={extracting || !selectedFile}
                  className="flex items-center gap-2 px-5 py-2 rounded-xl bg-orange-600 hover:bg-orange-700 text-white text-xs font-bold transition disabled:opacity-50 shadow-sm"
                >
                  {extracting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {extracting ? 'Extracting Template...' : 'Extract & Save Template'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Template View Modal */}
      <CustomTemplateViewModal
        isOpen={!!selectedViewTemplate}
        onClose={() => setSelectedViewTemplate(null)}
        template={selectedViewTemplate}
        onDelete={handleDelete}
      />
    </div>
  );
}
