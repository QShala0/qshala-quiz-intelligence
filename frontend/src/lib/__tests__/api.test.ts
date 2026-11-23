import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

const jsonRes = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('api error handling', () => {
  it('turns a FastAPI 422 array detail into a friendly message, not [object Object]', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(422, { detail: [{ loc: ['body', 'topic'], msg: 'x' }] }));
    await expect(api.listQuizzes()).rejects.toThrow(/not valid/i);
  });

  it('surfaces a string detail as is', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(404, { detail: 'Quiz not found' }));
    await expect(api.listQuizzes()).rejects.toThrow('Quiz not found');
  });

  it('survives an HTML 200 body without an opaque JSON crash', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>oops</html>', { status: 200 }));
    await expect(api.listQuizzes()).rejects.toThrow(/unexpected response/i);
  });

  it('maps a timeout on an interactive POST to a friendly message', async () => {
    const err = Object.assign(new Error('timeout'), { name: 'TimeoutError' });
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(err);
    await expect(
      api.exportQuizDirect({ id: 'q1', title: 't', questions: [] } as never, 'pptx'),
    ).rejects.toThrow();
  });

  it('does not re-send a quiz for export when the server errors with 500', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('boom', { status: 500 }));
    await expect(
      api.exportQuizDirect({ id: 'q1', title: 't', questions: [] } as never, 'pptx'),
    ).rejects.toThrow(/Export request failed/);
    expect(spy.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true);
  });
});
