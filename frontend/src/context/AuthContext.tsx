'use client';

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';
import { AuthUser, getMe, loginWithPassword, logout as apiLogout } from '@/lib/api';

interface AuthContextType {
  user: AuthUser | null;
  isLoading: boolean;
  error: string | null;
  refreshUser: () => Promise<void>;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshUser = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const currentUser = await getMe();
      setUser(currentUser);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    getMe()
      .then((currentUser) => {
        if (mounted) {
          setUser(currentUser);
          setIsLoading(false);
        }
      })
      .catch(() => {
        if (mounted) {
          setUser(null);
          setIsLoading(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  const login = useCallback(async (username: string, password: string): Promise<AuthUser> => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await loginWithPassword(username, password);
      setUser(res.user);
      return res.user;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Invalid username or password';
      setError(msg);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    setIsLoading(true);
    try {
      await apiLogout();
    } catch {
      // Ignore network errors on logout
    } finally {
      setUser(null);
      setIsLoading(false);
      if (typeof window !== 'undefined') {
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = '/login';
      }
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      isLoading,
      error,
      refreshUser,
      login,
      logout,
    }),
    [user, isLoading, error, refreshUser, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export function useIsAdmin(): boolean {
  const { user } = useAuth();
  return user?.role === 'admin' && user?.status === 'active';
}

interface AdminOnlyProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

export function AdminOnly({ children, fallback = null }: AdminOnlyProps) {
  const isAdmin = useIsAdmin();
  if (!isAdmin) {
    return <>{fallback}</>;
  }
  return <>{children}</>;
}
