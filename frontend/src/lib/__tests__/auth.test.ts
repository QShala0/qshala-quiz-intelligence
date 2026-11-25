import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  getMe,
  loginWithPassword,
  logout,
  updateUser,
  createUser,
  resetUserPassword,
  listAuditLogs,
} from '../api';

const jsonRes = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('auth api client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('getMe requests /auth/me with credentials included', async () => {
    const mockUser = {
      id: 'u1',
      display_name: 'Test Admin',
      email: 'admin@walnuts.co.in',
      username: null,
      team: 'Corporate',
      team_id: 'Corporate',
      role: 'admin',
      status: 'active',
      provider: 'microsoft',
      must_change_password: false,
      failed_logins: 0,
      locked_until: null,
      last_login_at: null,
      created_at: '2026-11-25T00:00:00Z',
      created_by: 'system',
    };

    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(200, mockUser));

    const result = await getMe();
    expect(result).toEqual(mockUser);
    expect(spy).toHaveBeenCalled();
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/me');
    expect(init?.credentials).toBe('include');
  });

  it('loginWithPassword posts username and password', async () => {
    const mockResponse = {
      message: 'Logged in successfully',
      user: {
        id: 'u2',
        username: 'consultant',
        display_name: 'Consultant',
        role: 'member',
        status: 'active',
      },
    };

    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(200, mockResponse));

    const result = await loginWithPassword('consultant', 'password123');
    expect(result.message).toBe('Logged in successfully');
    expect(result.user.username).toBe('consultant');

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/login');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('include');
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'consultant',
      password: 'password123',
    });
  });

  it('loginWithPassword surfaces backend error details on 401', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonRes(401, { detail: 'Invalid username or password' })
    );

    await expect(loginWithPassword('wrong', 'badpass')).rejects.toThrow(
      'Invalid username or password'
    );
  });

  it('logout posts to /auth/logout', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonRes(200, { message: 'Logged out successfully' }));

    const res = await logout();
    expect(res.message).toBe('Logged out successfully');

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/logout');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('include');
  });

  it('updateUser sends PATCH with JSON body', async () => {
    const updated = {
      id: 'target-1',
      display_name: 'Updated Name',
      role: 'admin',
      team_id: 'School',
      status: 'active',
    };

    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(200, updated));

    const res = await updateUser('target-1', { role: 'admin', team_id: 'School' });
    expect(res.role).toBe('admin');

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/users/target-1');
    expect(init?.method).toBe('PATCH');
    expect(JSON.parse(String(init?.body))).toEqual({
      role: 'admin',
      team_id: 'School',
    });
  });

  it('createUser sends POST with payload', async () => {
    const created = {
      id: 'new-1',
      display_name: 'New Person',
      email: 'new@walnuts.co.in',
      provider: 'microsoft',
      role: 'member',
      status: 'active',
    };

    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(201, created));

    const res = await createUser({
      display_name: 'New Person',
      email: 'new@walnuts.co.in',
      provider: 'microsoft',
      role: 'member',
      team_id: 'QShala',
    });
    expect(res.id).toBe('new-1');

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/users');
    expect(init?.method).toBe('POST');
  });

  it('resetUserPassword sends POST to /reset-password', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(200, { ok: true }));

    const res = await resetUserPassword('u-pwd', {
      new_password: 'newsecretpassword123',
      must_change_password: true,
    });
    expect(res.ok).toBe(true);

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/users/u-pwd/reset-password');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({
      new_password: 'newsecretpassword123',
      must_change_password: true,
    });
  });

  it('listAuditLogs includes page and limit in query string', async () => {
    const mockAudit = {
      items: [
        {
          id: 1,
          at: '2026-11-25T12:00:00Z',
          actor_user_id: 'admin-1',
          action: 'user_updated',
          target_user_id: 'user-2',
          detail: { team_id: ['School', 'QShala'] },
        },
      ],
      total: 1,
      page: 1,
      limit: 20,
    };

    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonRes(200, mockAudit));

    const res = await listAuditLogs({ page: 1, limit: 20 });
    expect(res.total).toBe(1);

    const [url] = spy.mock.calls[0];
    expect(String(url)).toContain('/auth/audit?page=1&limit=20');
  });
});
