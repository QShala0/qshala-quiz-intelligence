import type { Metadata } from 'next';
import { Plus_Jakarta_Sans } from 'next/font/google';
import './globals.css';
import AppShell from '../components/AppShell';
import { AuthProvider } from '../context/AuthContext';
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

export const metadata: Metadata = {
  title: 'QShala AI — Quiz Intelligence Platform',
  description: 'Create engaging quizzes using your trusted QShala knowledge base.',
  icons: {
    icon: '/icon.png',
    apple: '/icon.png',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`h-full bg-slate-50 text-slate-900 antialiased ${plusJakartaSans.variable}`}>
      <body className={`min-h-full flex bg-slate-50 font-sans ${plusJakartaSans.className}`}>
        <AuthProvider>
          <FeedbackProvider>
            <UploadProvider>
              <SidebarProvider>
                <AppShell>{children}</AppShell>
                <UploadProgressWidget />
              </SidebarProvider>
            </UploadProvider>
          </FeedbackProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
