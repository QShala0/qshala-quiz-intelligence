'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import QCat from '@/components/mascot/QCat';
import { Clock, RefreshCw, ArrowLeft } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';

export default function PendingPage() {
  const router = useRouter();
  const { user, refreshUser } = useAuth();
  const [checking, setChecking] = useState(false);

  const handleCheckStatus = async () => {
    setChecking(true);
    try {
      await refreshUser();
      if (user?.status === 'active') {
        router.push('/');
      }
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[#FBF7EE] p-4 sm:p-6">
      <div className="w-full max-w-[460px] rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] p-8 text-center shadow-xl shadow-amber-950/5">
        {/* Mascot */}
        <div className="mb-4 flex items-center justify-center">
          <QCat pose="think" size={64} alt="QShala QT Mascot" />
        </div>

        {/* Status Badge */}
        <div className="mx-auto mb-4 inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-[12px] font-semibold text-amber-800">
          <Clock className="h-3.5 w-3.5 text-amber-600" />
          <span>Awaiting Admin Approval</span>
        </div>

        <h1 className="text-[22px] font-bold tracking-tight text-[#1E222D]">
          Account Pending Approval
        </h1>

        <p className="mt-2 text-[13.5px] leading-relaxed text-[#4B5263]">
          Thank you for signing in! Your organization account has been registered with QShala AI,
          but an administrator needs to activate your access and assign your team before you can continue.
        </p>

        {user?.email && (
          <div className="mt-4 rounded-xl border border-[#E9DFC9]/80 bg-[#FFF6E0]/60 p-3 text-[12.5px] text-[#4B5263]">
            Signed in as <strong className="text-[#1E222D]">{user.email}</strong>
          </div>
        )}

        <div className="mt-6 flex flex-col gap-2.5 sm:flex-row">
          <button
            type="button"
            onClick={handleCheckStatus}
            disabled={checking}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[#1E222D] px-4 py-2.5 text-[13.5px] font-semibold text-[#FFFCF5] shadow-sm transition-all hover:bg-[#2B3040] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${checking ? 'animate-spin' : ''}`} />
            <span>Check Status</span>
          </button>

          <Link
            href="/login"
            className="flex items-center justify-center gap-1.5 rounded-xl border border-[#E9DFC9] bg-white px-4 py-2.5 text-[13.5px] font-semibold text-[#1E222D] transition-all hover:bg-[#FFF6E0] focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20"
          >
            <ArrowLeft className="h-4 w-4 text-[#686D7B]" />
            <span>Sign In</span>
          </Link>
        </div>

        <p className="mt-6 text-[11.5px] text-[#686D7B]">
          If this is urgent, please contact your team lead or QShala platform administrator.
        </p>
      </div>
    </div>
  );
}
