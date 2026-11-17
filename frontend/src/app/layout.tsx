import type { Metadata } from 'next';
import { Plus_Jakarta_Sans } from 'next/font/google';
import localFont from 'next/font/local';
import './globals.css';
import AppShell from '../components/AppShell';
import { SidebarProvider } from '../context/SidebarContext';
import { UploadProvider } from '../context/UploadContext';
import UploadProgressWidget from '../components/UploadProgressWidget';
import { FeedbackProvider } from '../components/ui';

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-plus-jakarta',
  display: 'swap',
});

// QShala brand typeface (converted from the Rebrand font family by scripts/build_qt_assets.py)
const causten = localFont({
  src: [
    { path: '../fonts/CaustenRound-400.woff2', weight: '400', style: 'normal' },
    { path: '../fonts/CaustenRound-500.woff2', weight: '500', style: 'normal' },
    { path: '../fonts/CaustenRound-600.woff2', weight: '600', style: 'normal' },
    { path: '../fonts/CaustenRound-700.woff2', weight: '700', style: 'normal' },
    { path: '../fonts/CaustenRound-800.woff2', weight: '800', style: 'normal' },
    { path: '../fonts/CaustenRound-900.woff2', weight: '900', style: 'normal' },
  ],
  variable: '--font-causten',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'QShala AI — Quiz Intelligence Platform',
  description: 'Create engaging quizzes using your trusted QShala knowledge base.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`h-full bg-slate-50 text-slate-900 antialiased ${causten.variable} ${plusJakartaSans.variable}`}>
      <body className="min-h-full flex bg-slate-50 font-sans">
        <FeedbackProvider>
          <UploadProvider>
            <SidebarProvider>
              <AppShell>{children}</AppShell>
              <UploadProgressWidget />
            </SidebarProvider>
          </UploadProvider>
        </FeedbackProvider>
      </body>
    </html>
  );
}
