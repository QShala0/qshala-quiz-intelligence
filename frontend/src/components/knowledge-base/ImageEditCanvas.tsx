'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Square, Paintbrush, Undo2, Redo2, RotateCcw, X, Loader2, AlertTriangle } from 'lucide-react';

// Edits one picture in the browser: blank out a box (white, black or pixelated) or paint over details with a brush.
// Nothing is stored here. Save hands back a new image file; the Edit images popup uploads it when that popup is saved,
// and the old stored file is deleted then only if nothing else uses it.

type Tool = 'box' | 'brush';
type Fill = 'white' | 'black' | 'pixelate';

const MAX_SIDE = 3000;             // larger pictures are scaled down when opened (keeps memory and file size sane)
const MAX_HISTORY = 15;            // undo steps kept in memory
const PIXEL_BLOCKS = 14;           // how coarse "pixelate" is: the box is redrawn ~14 blocks across

interface Props {
  title: string;                   // "Edit picture" / "Edit a copy"
  load: () => Promise<Blob>;       // the original picture's bytes
  maxBytes: number;                // the server's upload limit; the saved file is kept under it
  requireChange?: boolean;         // editing in place needs a change; a copy may be kept as it is
  onCancel: () => void;
  onSave: (file: File) => void;
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

// PNG when the picture was a PNG and it fits, else JPEG, stepping quality and then size down until it fits the limit.
async function encodeUnderLimit(src: HTMLCanvasElement, preferPng: boolean, maxBytes: number): Promise<Blob> {
  if (preferPng) {
    const png = await canvasToBlob(src, 'image/png');
    if (png && png.size <= maxBytes) return png;
  }
  let canvas = src;
  for (let attempt = 0; attempt < 8; attempt++) {
    for (const q of [0.92, 0.85, 0.75]) {
      const jpg = await canvasToBlob(canvas, 'image/jpeg', q);
      if (jpg && jpg.size <= maxBytes) return jpg;
    }
    const next = document.createElement('canvas');
    next.width = Math.max(1, Math.round(canvas.width * 0.8));
    next.height = Math.max(1, Math.round(canvas.height * 0.8));
    const ctx = next.getContext('2d');
    if (!ctx) break;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, next.width, next.height);
    ctx.drawImage(canvas, 0, 0, next.width, next.height);
    canvas = next;
  }
  throw new Error('The edited picture is too large to save.');
}

export default function ImageEditCanvas({ title, load, maxBytes, requireChange = true, onCancel, onSave }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const originalRef = useRef<ImageData | null>(null);
  const undoRef = useRef<ImageData[]>([]);
  const redoRef = useRef<ImageData[]>([]);
  const dragRef = useRef<{ x: number; y: number; lastX: number; lastY: number } | null>(null);
  const sourceTypeRef = useRef<string>('image/png');

  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>('box');
  const [fill, setFill] = useState<Fill>('white');
  const [brush, setBrush] = useState(24);
  const [counts, setCounts] = useState({ undo: 0, redo: 0 });
  const [saving, setSaving] = useState(false);

  const syncCounts = () => setCounts({ undo: undoRef.current.length, redo: redoRef.current.length });

  // Load the picture into the canvas once.
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    load()
      .then((blob) => {
        if (cancelled) return;
        sourceTypeRef.current = blob.type || 'image/png';
        objectUrl = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          if (cancelled) return;
          const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
          const w = Math.max(1, Math.round(img.naturalWidth * scale));
          const h = Math.max(1, Math.round(img.naturalHeight * scale));
          const canvas = canvasRef.current;
          const overlay = overlayRef.current;
          const ctx = canvas?.getContext('2d', { willReadFrequently: true });
          if (!canvas || !overlay || !ctx) return;
          canvas.width = overlay.width = w;
          canvas.height = overlay.height = h;
          ctx.drawImage(img, 0, 0, w, h);
          originalRef.current = ctx.getImageData(0, 0, w, h);
          setStatus('ready');
        };
        img.onerror = () => {
          if (!cancelled) {
            setStatus('error');
            setError('This picture could not be opened for editing.');
          }
        };
        img.src = objectUrl;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setStatus('error');
        setError(e instanceof Error && e.message ? e.message : 'This picture could not be loaded.');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [load]);

  const ctx2d = () => canvasRef.current?.getContext('2d', { willReadFrequently: true }) ?? null;

  const snapshot = () => {
    const canvas = canvasRef.current;
    const ctx = ctx2d();
    if (!canvas || !ctx) return;
    undoRef.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    if (undoRef.current.length > MAX_HISTORY) undoRef.current.shift();
    redoRef.current = [];
    syncCounts();
  };

  const restore = (from: React.MutableRefObject<ImageData[]>, to: React.MutableRefObject<ImageData[]>) => {
    const canvas = canvasRef.current;
    const ctx = ctx2d();
    const data = from.current.pop();
    if (!canvas || !ctx || !data) return;
    to.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    ctx.putImageData(data, 0, 0);
    syncCounts();
  };
  const undo = useCallback(() => restore(undoRef, redoRef), []); // eslint-disable-line react-hooks/exhaustive-deps
  const redo = useCallback(() => restore(redoRef, undoRef), []); // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => {
    const ctx = ctx2d();
    if (!ctx || !originalRef.current) return;
    snapshot();
    ctx.putImageData(originalRef.current, 0, 0);
  };

  // Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl+Y redo.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((k === 'z' && e.shiftKey) || k === 'y') { e.preventDefault(); redo(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo]);

  // Pointer position in canvas pixels (the canvas is shown scaled down to fit the screen).
  const toCanvas = (e: React.PointerEvent) => {
    const canvas = canvasRef.current as HTMLCanvasElement;
    const r = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(canvas.width, ((e.clientX - r.left) / r.width) * canvas.width)),
      y: Math.max(0, Math.min(canvas.height, ((e.clientY - r.top) / r.height) * canvas.height)),
    };
  };
  const displayScale = () => {
    const canvas = canvasRef.current as HTMLCanvasElement;
    return canvas.width / Math.max(1, canvas.getBoundingClientRect().width);
  };

  const colour = fill === 'black' ? '#000000' : '#ffffff';

  const applyBox = (x0: number, y0: number, x1: number, y1: number) => {
    const ctx = ctx2d();
    const canvas = canvasRef.current;
    if (!ctx || !canvas) return;
    const x = Math.round(Math.min(x0, x1));
    const y = Math.round(Math.min(y0, y1));
    const w = Math.round(Math.abs(x1 - x0));
    const h = Math.round(Math.abs(y1 - y0));
    if (w < 2 || h < 2) return;
    snapshot();
    if (fill === 'pixelate') {
      const block = Math.max(4, Math.round(Math.max(w, h) / PIXEL_BLOCKS));
      const small = document.createElement('canvas');
      small.width = Math.max(1, Math.round(w / block));
      small.height = Math.max(1, Math.round(h / block));
      const sctx = small.getContext('2d');
      if (!sctx) return;
      sctx.drawImage(canvas, x, y, w, h, 0, 0, small.width, small.height);
      ctx.save();
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(small, 0, 0, small.width, small.height, x, y, w, h);
      ctx.restore();
    } else {
      ctx.fillStyle = colour;
      ctx.fillRect(x, y, w, h);
    }
  };

  const drawOverlayBox = (x0: number, y0: number, x1: number, y1: number) => {
    const o = overlayRef.current;
    const octx = o?.getContext('2d');
    if (!o || !octx) return;
    octx.clearRect(0, 0, o.width, o.height);
    const lw = Math.max(1, 2 * displayScale());
    octx.lineWidth = lw;
    octx.setLineDash([6 * lw, 4 * lw]);
    octx.strokeStyle = '#2563eb';
    octx.fillStyle = fill === 'black' ? 'rgba(0,0,0,0.35)' : fill === 'white' ? 'rgba(255,255,255,0.45)' : 'rgba(37,99,235,0.15)';
    const x = Math.min(x0, x1), y = Math.min(y0, y1), w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    octx.fillRect(x, y, w, h);
    octx.strokeRect(x, y, w, h);
  };
  const clearOverlay = () => {
    const o = overlayRef.current;
    o?.getContext('2d')?.clearRect(0, 0, o.width, o.height);
  };

  const brushWidth = () => Math.max(2, brush * displayScale());

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (status !== 'ready' || saving) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = toCanvas(e);
    dragRef.current = { x: p.x, y: p.y, lastX: p.x, lastY: p.y };
    if (tool === 'brush') {
      snapshot();
      const ctx = ctx2d();
      if (!ctx) return;
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.arc(p.x, p.y, brushWidth() / 2, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const p = toCanvas(e);
    if (tool === 'box') {
      drawOverlayBox(d.x, d.y, p.x, p.y);
    } else {
      const ctx = ctx2d();
      if (!ctx) return;
      ctx.strokeStyle = colour;
      ctx.lineWidth = brushWidth();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(d.lastX, d.lastY);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
    d.lastX = p.x;
    d.lastY = p.y;
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    if (tool === 'box') {
      const p = toCanvas(e);
      clearOverlay();
      applyBox(d.x, d.y, p.x, p.y);
    }
  };

  const handleSave = async () => {
    const canvas = canvasRef.current;
    if (!canvas || saving) return;
    setSaving(true);
    setError(null);
    try {
      const blob = await encodeUnderLimit(canvas, sourceTypeRef.current === 'image/png', maxBytes);
      const ext = blob.type === 'image/png' ? 'png' : 'jpg';
      onSave(new File([blob], `edited-${Date.now()}.${ext}`, { type: blob.type }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the edited picture.');
      setSaving(false);
    }
  };

  // Brush and pixelate do not mix: pixelate is a box-only effect.
  const fills: { id: Fill; label: string }[] = tool === 'box'
    ? [{ id: 'white', label: 'White' }, { id: 'black', label: 'Black' }, { id: 'pixelate', label: 'Pixelate' }]
    : [{ id: 'white', label: 'White' }, { id: 'black', label: 'Black' }];
  const activeFill: Fill = fill;
  const selectTool = (t: Tool) => {
    setTool(t);
    if (t === 'brush' && fill === 'pixelate') setFill('white');
  };

  const toolBtn = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-semibold transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
      active ? 'bg-blue-600 text-white' : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50'}`;

  return (
    <div className="fixed inset-0 z-70 flex items-center justify-center bg-black/80 p-3" role="dialog" aria-modal="true" aria-label={title}
         data-testid="image-edit-canvas">
      <div className="flex max-h-[96vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-slate-50 shadow-2xl">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
          <span className="mr-2 text-[14px] font-bold text-slate-800">{title}</span>
          <div className="flex gap-1.5" role="group" aria-label="Tool">
            <button type="button" className={toolBtn(tool === 'box')} onClick={() => selectTool('box')} aria-pressed={tool === 'box'}>
              <Square className="h-3.5 w-3.5" /> Blank out box
            </button>
            <button type="button" className={toolBtn(tool === 'brush')} onClick={() => selectTool('brush')} aria-pressed={tool === 'brush'}>
              <Paintbrush className="h-3.5 w-3.5" /> Erase brush
            </button>
          </div>
          <div className="flex gap-1.5" role="group" aria-label="Fill">
            {fills.map((f) => (
              <button key={f.id} type="button" className={toolBtn(activeFill === f.id)} onClick={() => setFill(f.id)} aria-pressed={activeFill === f.id}>
                {f.label}
              </button>
            ))}
          </div>
          {tool === 'brush' && (
            <label className="flex items-center gap-2 text-[12px] font-semibold text-slate-600">
              Size
              <input type="range" min={6} max={80} value={brush} onChange={(e) => setBrush(Number(e.target.value))} aria-label="Brush size" />
            </label>
          )}
          <div className="ml-auto flex gap-1.5">
            <button type="button" className={toolBtn(false)} onClick={undo} disabled={counts.undo === 0} title="Undo (Ctrl+Z)" aria-label="Undo">
              <Undo2 className="h-3.5 w-3.5" />
            </button>
            <button type="button" className={toolBtn(false)} onClick={redo} disabled={counts.redo === 0} title="Redo (Ctrl+Y)" aria-label="Redo">
              <Redo2 className="h-3.5 w-3.5" />
            </button>
            <button type="button" className={toolBtn(false)} onClick={reset} disabled={status !== 'ready'} title="Back to the original picture">
              <RotateCcw className="h-3.5 w-3.5" /> Reset
            </button>
          </div>
        </div>

        <div className="relative flex min-h-[240px] flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(#e2e8f0_0_25%,#f8fafc_0_50%)] bg-[length:20px_20px] p-4">
          {status === 'loading' && (
            <div className="absolute inset-0 flex items-center justify-center gap-2 text-[13px] font-semibold text-slate-600">
              <Loader2 className="h-4 w-4 animate-spin" /> Opening the picture…
            </div>
          )}
          <div className={`relative ${status === 'ready' ? '' : 'invisible'}`}>
            <canvas
              ref={canvasRef}
              className="block max-h-[68vh] max-w-full select-none"
              style={{ touchAction: 'none', cursor: tool === 'box' ? 'crosshair' : 'cell' }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              data-testid="edit-canvas"
            />
            <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
          </div>
        </div>

        {error && (
          <div role="alert" className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-3">
          <span className="text-[11.5px] text-slate-500">
            {tool === 'box' ? 'Drag over the area to blank out.' : 'Paint over what you want to hide.'} Nothing is stored until
            you save the Edit images window.
          </span>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer disabled:opacity-50">
              <X className="h-3.5 w-3.5" /> Cancel
            </button>
            <button type="button" onClick={handleSave} disabled={saving || status !== 'ready' || (requireChange && counts.undo === 0)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-700 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              title={requireChange && counts.undo === 0 ? 'Make a change first' : undefined}>
              {saving ? 'Preparing…' : 'Use this picture'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
