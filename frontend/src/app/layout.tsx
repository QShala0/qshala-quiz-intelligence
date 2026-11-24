import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';
import AppShell from '../components/AppShell';
import { SidebarProvider } from '../context/SidebarContext';
import { UploadProvider } from '../context/UploadContext';
import UploadProgressWidget from '../components/UploadProgressWidget';
import { FeedbackProvider } from '../components/ui';

const caustenRound = localFont({
  src: [
    {
      path: '../fonts/CaustenRound-400.woff2',
      weight: '400',
      style: 'normal',
    },
    {
      path: '../fonts/CaustenRound-500.woff2',
      weight: '500',
      style: 'normal',
    },
    {
      path: '../fonts/CaustenRound-600.woff2',
      weight: '600',
      style: 'normal',
    },
    {
      path: '../fonts/CaustenRound-700.woff2',
      weight: '700',
      style: 'normal',
    },
    {
      path: '../fonts/CaustenRound-800.woff2',
      weight: '800',
      style: 'normal',
    },
    {
      path: '../fonts/CaustenRound-900.woff2',
      weight: '900',
      style: 'normal',
    },
  ],
  variable: '--font-causten-round',
  display: 'swap',
});

export const metadata: Metadata = {
  metadataBase: new URL('https://qshala-quiz-intelligence-theta.vercel.app'),
  title: 'QShala AI — Quiz Intelligence Platform',
  description: 'Questions • Quriosity • Quests — Create engaging quizzes using your trusted QShala knowledge base.',
  icons: {
    icon: [
      { url: '/favicon.ico', sizes: '48x48' },
      { url: '/icon.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [
      { url: '/apple-icon.png', sizes: '180x180', type: 'image/png' },
    ],
  },
  openGraph: {
    title: 'QShala AI — Quiz Intelligence Platform',
    description: 'Questions • Quriosity • Quests — Create engaging quizzes using your trusted QShala knowledge base.',
    images: ['/brand/og.png'],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`h-full antialiased ${caustenRound.variable}`}>
      <body className={`min-h-full flex font-sans ${caustenRound.className}`}>
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
