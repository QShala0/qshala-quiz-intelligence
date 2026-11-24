import type { Metadata } from 'next';
import { Plus_Jakarta_Sans } from 'next/font/google';
import './globals.css';
import AppShell from '../components/AppShell';
import { SidebarProvider } from '../context/SidebarContext';
import { UploadProvider } from '../context/UploadContext';
import UploadProgressWidget from '../components/UploadProgressWidget';
import { FeedbackProvider } from '../components/ui';

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700', '800'],
  variable: '--font-plus-jakarta',
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
    <html lang="en" className={`h-full antialiased ${plusJakartaSans.variable}`}>
      <body className={`min-h-full flex font-sans ${plusJakartaSans.className}`}>
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
