'use client';

import React, { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import QCat from '@/components/mascot/QCat';
import { Lock, User, AlertCircle, ArrowRight, Loader2 } from 'lucide-react';

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, login } = useAuth();

  const redirectPath = searchParams.get('redirect') || '/';
  const urlError = searchParams.get('error');

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const initialUrlError =
    urlError === 'blocked'
      ? 'Your account has been deactivated. Please contact an administrator.'
      : urlError === 'unauthorized'
      ? 'Sign in failed. Your organization email domain may not be authorized.'
      : null;

  const errorMessage = formError || initialUrlError;

  useEffect(() => {
    if (user && user.status === 'active') {
      router.replace(redirectPath);
    }
  }, [user, redirectPath, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setFormError('Please enter both username and password.');
      return;
    }

    setIsLoading(true);
    setFormError(null);

    try {
      const loggedInUser = await login(username.trim(), password);
      if (loggedInUser.status === 'pending') {
        router.push('/pending');
      } else if (loggedInUser.status === 'blocked') {
        setFormError('Your account has been deactivated. Please contact an administrator.');
      } else {
        router.push(redirectPath);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Invalid username or password';
      setFormError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-[420px] rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] p-8 shadow-xl shadow-amber-950/5">
      {/* Brand & Mascot */}
      <div className="mb-6 flex flex-col items-center text-center">
        <div className="mb-2 flex items-center justify-center">
          <QCat pose="think" size={56} alt="QShala QT Mascot" />
        </div>
        <h1 className="text-[22px] font-bold tracking-tight text-[#1E222D]">
          Sign in to QShala AI
        </h1>
        <p className="mt-1 text-[13px] text-[#4B5263]">
          Quiz Intelligence & Content Knowledge Base
        </p>
      </div>

      {/* Error Alert */}
      {errorMessage && (
        <div
          role="alert"
          className="mb-5 flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50/80 p-3.5 text-[13px] text-red-800"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
          <div className="leading-snug">{errorMessage}</div>
        </div>
      )}

      {/* Microsoft Sign In */}
      <a
        href="/api/v1/auth/microsoft/login"
        className="group relative flex w-full items-center justify-center gap-3 rounded-xl border border-[#E9DFC9] bg-white px-4 py-2.5 text-[14px] font-semibold text-[#1E222D] shadow-sm transition-all hover:border-[#D8CBB0] hover:bg-[#FFF6E0] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
      >
        <svg className="h-4 w-4 shrink-0" viewBox="0 0 21 21">
          <rect x="1" y="1" width="9" height="9" fill="#f25022" />
          <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
          <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
          <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
        </svg>
        <span>Sign in with Microsoft</span>
      </a>

      {/* Divider */}
      <div className="relative my-6 flex items-center justify-center">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-[#E9DFC9]" />
        </div>
        <span className="relative bg-[#FFFCF5] px-3 text-[11px] font-semibold uppercase tracking-wider text-[#686D7B]">
          or sign in with password
        </span>
      </div>

      {/* Password Form */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="username"
            className="block text-[12px] font-semibold text-[#1E222D]"
          >
            Username or Email
          </label>
          <div className="relative mt-1.5">
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#686D7B]">
              <User className="h-4 w-4" />
            </div>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. user or yourname@walnuts.co.in"
              className="w-full rounded-xl border border-[#E9DFC9] bg-white py-2.5 pl-9 pr-3.5 text-[13.5px] text-[#1E222D] placeholder-[#686D7B]/60 transition-all focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
            />
          </div>
        </div>

        <div>
          <label
            htmlFor="password"
            className="block text-[12px] font-semibold text-[#1E222D]"
          >
            Password
          </label>
          <div className="relative mt-1.5">
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#686D7B]">
              <Lock className="h-4 w-4" />
            </div>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full rounded-xl border border-[#E9DFC9] bg-white py-2.5 pl-9 pr-3.5 text-[13.5px] text-[#1E222D] placeholder-[#686D7B]/60 transition-all focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-[#1E222D] px-4 py-2.5 text-[14px] font-semibold text-[#FFFCF5] shadow-sm transition-all hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-[#FFFCF5]" />
              <span>Signing in...</span>
            </>
          ) : (
            <>
              <span>Sign in</span>
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </form>

      {/* Footer Info */}
      <div className="mt-6 border-t border-[#E9DFC9]/60 pt-4 text-center text-[12px] text-[#686D7B]">
        <span>For internal QShala & Walnuts staff only.</span>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[#FBF7EE] p-4 sm:p-6">
      <Suspense
        fallback={
          <div className="flex items-center gap-2 text-sm text-[#4B5263]">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span>Loading sign in...</span>
          </div>
        }
      >
        <LoginForm />
      </Suspense>
    </div>
  );
}
