import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const MIME_MAP: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
  '.m4a': 'audio/mp4',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
};

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path: pathSegments } = await context.params;

  // Reject path segments that attempt directory traversal
  if (pathSegments.some(seg => seg === '..' || seg === '.' || seg.includes('\0'))) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 });
  }

  const relativePath = pathSegments.join('/');

  // Search candidate root directories
  const rootDirs = [
    path.join(process.cwd(), 'public', 'static'),
    path.join(process.cwd(), 'public'),
    path.join(process.cwd(), '..', 'backend', 'storage'),
    path.join(process.cwd(), '..', 'backend', 'storage', 'slides'),
    path.join('/tmp', 'qshala_storage'),
    path.join('/tmp', 'qshala_storage', 'slides'),
  ];

  const candidatePaths = rootDirs.map(root => {
    const joined = path.join(/*turbopackIgnore: true*/ root, relativePath);
    const resolved = path.resolve(/*turbopackIgnore: true*/ joined);
    const resolvedRoot = path.resolve(/*turbopackIgnore: true*/ root);
    // Containment check: resolved path must stay inside its root
    if (!resolved.startsWith(resolvedRoot + path.sep) && resolved !== resolvedRoot) {
      return null;
    }
    return resolved;
  }).filter((p): p is string => p !== null);

  for (const filePath of candidatePaths) {
    if (fs.existsSync(/*turbopackIgnore: true*/ filePath)) {
      try {
        const stat = fs.statSync(/*turbopackIgnore: true*/ filePath);
        if (!stat.isFile()) continue;

        const ext = path.extname(filePath).toLowerCase();
        const contentType = MIME_MAP[ext] || 'application/octet-stream';
        const range = req.headers.get('range');

        // Handle HTTP 206 Partial Content for video/audio streaming
        if (range) {
          const parts = range.replace(/bytes=/, '').split('-');
          const start = parseInt(parts[0], 10);
          const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
          const chunkSize = end - start + 1;

          const fileStream = fs.createReadStream(/*turbopackIgnore: true*/ filePath, { start, end });
          // Convert Node readable stream to Web ReadableStream
          const webStream = new ReadableStream({
            start(controller) {
              fileStream.on('data', (chunk) => controller.enqueue(chunk));
              fileStream.on('end', () => controller.close());
              fileStream.on('error', (err) => controller.error(err));
            },
          });

          return new NextResponse(webStream, {
            status: 206,
            headers: {
              'Content-Range': `bytes ${start}-${end}/${stat.size}`,
              'Accept-Ranges': 'bytes',
              'Content-Length': chunkSize.toString(),
              'Content-Type': contentType,
              'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
            },
          });
        }

        const fullStream = fs.createReadStream(/*turbopackIgnore: true*/ filePath);
        const webFullStream = new ReadableStream({
          start(controller) {
            fullStream.on('data', (chunk) => controller.enqueue(chunk));
            fullStream.on('end', () => controller.close());
            fullStream.on('error', (err) => controller.error(err));
          },
        });
        return new NextResponse(webFullStream, {
          status: 200,
          headers: {
            'Content-Type': contentType,
            'Content-Length': stat.size.toString(),
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
          },
        });
      } catch (err) {
        console.error('Error serving media file from disk:', filePath, err);
      }
    }
  }

  // Fallback: Proxy to backend API if internal API URL is available
  const internalApi = process.env.INTERNAL_API_URL;
  if (internalApi) {
    try {
      const backendUrl = `${internalApi.replace(/\/api.*$/, '')}/static/${relativePath}`;
      const resp = await fetch(backendUrl);
      if (resp.ok) {
        const blob = await resp.blob();
        return new NextResponse(blob, {
          status: resp.status,
          headers: {
            'Content-Type': resp.headers.get('Content-Type') || 'application/octet-stream',
            'Cache-Control': 'public, max-age=86400',
          },
        });
      }
    } catch (err) {
      console.warn('Fallback proxy to backend failed:', err);
    }
  }

  return NextResponse.json({ error: 'Media file not found', path: relativePath }, { status: 404 });
}
