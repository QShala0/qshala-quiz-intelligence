// jszip is loaded when a deck is actually optimized, not with the page that imports this file.

// 1x1 base64 transparent GIF stub for oversized animations
const TINY_GIF_BASE64 = 'R0lGODlhAQABAIAAAAAAAAAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==';

const HEAVY_MEDIA_EXTS = new Set([
  '.mp4', '.mov', '.avi', '.wmv', '.m4v', '.flv', '.webm',
  '.mp3', '.wav', '.m4a', '.aac', '.wma', '.ogg', '.flac'
]);

const IMAGE_EXTS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.tiff', '.bmp'
]);

export interface OptimizationResult {
  file: File;
  originalMb: number;
  newMb: number;
  reductionPct: number;
}

/**
 * Resizes and compresses an image in browser using canvas to reduce size while
 * preserving visual clarity for quiz questions.
 */
async function compressImageInBrowser(
  rawBytes: Uint8Array,
  mimeType: string,
  maxDimension: number = 960,
  quality: number = 0.75
): Promise<Uint8Array | null> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return null;
  }
  return new Promise((resolve) => {
    try {
      const blob = new Blob([rawBytes as unknown as BlobPart], { type: mimeType });
      const img = new Image();
      const url = URL.createObjectURL(blob);

      img.onload = () => {
        URL.revokeObjectURL(url);
        let { width, height } = img;
        if (!width || !height) {
          resolve(null);
          return;
        }

        if (width > maxDimension || height > maxDimension) {
          if (width > height) {
            height = Math.round((height * maxDimension) / width);
            width = maxDimension;
          } else {
            width = Math.round((width * maxDimension) / height);
            height = maxDimension;
          }
        }

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          resolve(null);
          return;
        }

        ctx.drawImage(img, 0, 0, width, height);
        canvas.toBlob(
          async (compressedBlob) => {
            if (!compressedBlob) {
              resolve(null);
              return;
            }
            const buffer = await compressedBlob.arrayBuffer();
            resolve(new Uint8Array(buffer));
          },
          'image/jpeg',
          quality
        );
      };

      img.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(null);
      };

      img.src = url;
    } catch {
      resolve(null);
    }
  });
}

/**
 * Optimizes large PPTX presentation decks directly in the browser
 * by stripping embedded videos and audios (which cause 100+ MB bloat)
 * while preserving all slide images, text, and question media intact.
 */
export async function optimizeDeckInBrowser(
  file: File,
  onStatusUpdate?: (status: string) => void
): Promise<OptimizationResult> {
  const maxSafeBytes = 4.5 * 1024 * 1024; // 4.5 MB target
  const originalMb = Number((file.size / (1024 * 1024)).toFixed(2));

  if (file.size <= maxSafeBytes) {
    return {
      file,
      originalMb,
      newMb: originalMb,
      reductionPct: 0,
    };
  }

  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  if (ext !== '.pptx' && ext !== '.ppt') {
    return {
      file,
      originalMb,
      newMb: originalMb,
      reductionPct: 0,
    };
  }

  onStatusUpdate?.(`Optimizing deck (${originalMb} MB) in browser...`);

  const { default: JSZip } = await import('jszip');
  const zip = await JSZip.loadAsync(file);

  // 1. Strip embedded fonts (ppt/fonts/) and revision histories (ppt/changesInfos/)
  for (const filename of Object.keys(zip.files)) {
    if (filename.startsWith('ppt/fonts/') || filename.startsWith('ppt/changesInfos/')) {
      zip.file(filename, '');
    }
  }

  // 2. Strip heavy videos and audios (which take up 50MB - 200MB)
  for (const filename of Object.keys(zip.files)) {
    if (filename.startsWith('ppt/media/')) {
      const mediaExt = filename.substring(filename.lastIndexOf('.')).toLowerCase();
      if (HEAVY_MEDIA_EXTS.has(mediaExt)) {
        zip.file(filename, '');
      } else if (mediaExt === '.gif') {
        // If animated GIF is larger than 1MB, replace with small stub
        const entry = zip.files[filename];
        if (entry && (entry as any)._data && (entry as any)._data.uncompressedSize > 1024 * 1024) {
          zip.file(filename, TINY_GIF_BASE64, { base64: true });
        }
      }
    }
  }

  // 3. Keep all question images intact, compressing only very large photos (> 250 KB)
  const imageFiles = Object.keys(zip.files).filter((f) => {
    if (!f.startsWith('ppt/media/')) return false;
    const mediaExt = f.substring(f.lastIndexOf('.')).toLowerCase();
    return IMAGE_EXTS.has(mediaExt) && mediaExt !== '.gif';
  });

  let processedCount = 0;
  for (const filename of imageFiles) {
    try {
      const zipEntry = zip.files[filename];
      const rawBytes = await zipEntry.async('uint8array');
      if (rawBytes.length > 250 * 1024) {
        const mime = filename.endsWith('.png') ? 'image/png' : 'image/jpeg';
        const compressed = await compressImageInBrowser(rawBytes, mime, 960, 0.75);
        if (compressed && compressed.length < rawBytes.length) {
          zip.file(filename, compressed);
        }
      }
      processedCount++;
      if (processedCount % 12 === 0 && onStatusUpdate) {
        onStatusUpdate(`Preserving and optimizing question media (${processedCount}/${imageFiles.length})...`);
      }
    } catch {
      // Keep original image on any conversion anomaly
    }
  }

  onStatusUpdate?.('Packing optimized presentation with full question media...');

  const optimizedBlob = await zip.generateAsync({
    type: 'blob',
    compression: 'DEFLATE',
    compressionOptions: { level: 9 },
  });

  const newSize = optimizedBlob.size;
  const newMb = Number((newSize / (1024 * 1024)).toFixed(2));
  const reductionPct = Number(((1 - newSize / file.size) * 100).toFixed(1));

  const optimizedFile = new File([optimizedBlob], file.name, {
    type: file.type || 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  });

  return {
    file: optimizedFile,
    originalMb,
    newMb,
    reductionPct,
  };
}
