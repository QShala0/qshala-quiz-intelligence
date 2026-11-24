'use client';

import Link from 'next/link';
import { FileText, Layers, HelpCircle, Tag, ArrowRight } from 'lucide-react';
import { DashboardStats } from '../lib/types';

interface Props {
  stats: DashboardStats | null;
  loading?: boolean;
}

export default function StatsOverview({ stats, loading }: Props) {
  const cards = [
    {
      label: 'Documents',
      value: loading ? '-' : (stats?.total_documents ?? 0).toLocaleString(),
      sub: 'Uploaded slide decks',
      icon: FileText,
      iconBg: 'bg-purple-50 text-purple-600',
      href: '/uploads',
    },
    {
      label: 'Slides',
      value: loading ? '-' : (stats?.total_slides ?? 0).toLocaleString(),
      sub: 'Across all presentations',
      icon: Layers,
      iconBg: 'bg-emerald-50 text-emerald-600',
      href: '/uploads',
    },
    {
      label: 'Extracted Questions',
      value: loading ? '-' : (stats?.total_questions ?? 0).toLocaleString(),
      sub: 'From historical quizzes',
      icon: HelpCircle,
      iconBg: 'bg-blue-50 text-blue-600',
      href: '/knowledge-base',
    },
    {
      label: 'Topics',
      value: loading ? '-' : (stats?.total_topics ?? 0).toLocaleString(),
      sub: 'Categorized topics',
      icon: Tag,
      iconBg: 'bg-amber-50 text-amber-600',
      href: '/knowledge-base',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card, i) => {
        const Icon = card.icon;
        return (
          <Link
            key={i}
            href={card.href}
            className="group flex flex-col justify-between rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50 hover:border-slate-300 hover:shadow-md transition-all"
          >
            <div>
              {/* Icon */}
              <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${card.iconBg} mb-3.5`}>
                <Icon className="h-4 w-4" />
              </div>

              {/* Number */}
              <div className="text-[26px] font-bold tracking-tight text-slate-900 leading-none">
                {loading ? (
                  <div className="h-7 w-20 animate-pulse rounded bg-slate-100" />
                ) : (
                  card.value
                )}
              </div>

              {/* Label */}
              <p className="mt-1.5 text-[13px] font-semibold text-slate-700">{card.label}</p>
            </div>

            {/* Bottom link with arrow */}
            <div className="mt-4 flex items-center justify-between text-[12px] text-slate-400 font-normal group-hover:text-slate-600 transition-colors pt-2 border-t border-slate-50">
              <span>{card.sub}</span>
              <ArrowRight className="h-3 w-3 group-hover:translate-x-0.5 transition-transform" />
            </div>
          </Link>
        );
      })}
    </div>
  );
}
