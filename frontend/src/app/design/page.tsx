'use client';

/**
 * Living Styleguide & Brand Kit
 * Visual source of truth for the QShala June 2026 Brand Guidelines.
 */
import { useState } from 'react';
import {
  QT,
  QT_POSES,
  Logo,
  Button,
  Card,
  Badge,
  Modal,
  Progress,
  Confetti,
  PageHeader,
  Doodles,
  EmptyState,
  LoadingState,
  ErrorState,
  useToast,
  useConfirm,
} from '../../components/ui';

const SWATCHES = [
  { name: 'Joy of Quest (Sun)', hex: '#FDB913', role: 'Hero Brand Accent', cls: 'bg-sun text-ink' },
  { name: 'Qurious Sky (Sky)', hex: '#30B2E7', role: 'Blue Accent', cls: 'bg-sky text-ink' },
  { name: 'Always Questioning (Green)', hex: '#75B543', role: 'Green Accent', cls: 'bg-grass text-white' },
  { name: 'Ink (Black)', hex: '#000000', role: 'Text & Headings', cls: 'bg-ink text-white' },
  { name: 'Cream (Page Canvas)', hex: '#FFF6E1', role: 'Global Page Surface', cls: 'bg-cream text-ink border border-sun/30' },
  { name: 'Paper (White)', hex: '#FFFFFF', role: 'Cards on Cream', cls: 'bg-white text-ink border border-slate-200' },
  { name: 'Warm Grey', hex: '#5F5F66', role: 'Secondary Text (AA)', cls: 'bg-[#5F5F66] text-white' },
  { name: 'Functional Error (Red)', hex: '#D62828', role: 'Validation & Danger', cls: 'bg-berry text-white' },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-12">
      <h2 className="section-bar mb-5 text-[14px]">{title}</h2>
      {children}
    </section>
  );
}

export default function DesignPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const [modal, setModal] = useState(false);
  const [pct, setPct] = useState(45);
  const [burst, setBurst] = useState(0);

  return (
    <div className="mx-auto max-w-5xl p-6 sm:p-10 space-y-2">
      <PageHeader
        title="Living Brand Guide"
        subtitle="Official June 2026 Brand Guidelines: colours, typography, brand lockups, and QT poses."
        pose="sherlock"
      />

      {/* 1. Official Palette */}
      <Section title="Official Brand Palette">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {SWATCHES.map((s) => (
            <div
              key={s.name}
              className="rounded-[20px] border border-sun/30 bg-white p-3 shadow-sticker-sm space-y-2"
            >
              <div className={`h-16 rounded-xl flex items-center justify-center font-bold text-xs ${s.cls}`}>
                {s.hex}
              </div>
              <div>
                <p className="text-xs font-bold text-ink">{s.name}</p>
                <p className="text-[11px] text-slate-500 font-normal">{s.role}</p>
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* 2. Official Logo Lockups */}
      <Section title="Official Logo Lockups (with ® and Clear Space)">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-8 rounded-[20px] border border-sun/30 bg-white p-6 shadow-sticker-sm">
            <div className="p-3 bg-cream rounded-xl border border-sun/20">
              <Logo tagline />
            </div>
            <div className="p-3 bg-white rounded-xl border border-slate-200">
              <Logo variant="stacked" />
            </div>
            <div className="p-3 bg-white rounded-xl border border-slate-200">
              <Logo collapsed />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="flex items-center justify-center p-6 rounded-[20px] bg-sky border-2 border-ink shadow-sticker-sm">
              <Logo variant="white" />
            </div>
            <div className="flex items-center justify-center p-6 rounded-[20px] bg-sun border-2 border-ink shadow-sticker-sm">
              <Logo variant="horizontal" />
            </div>
            <div className="flex items-center justify-center p-6 rounded-[20px] bg-grass border-2 border-ink shadow-sticker-sm">
              <Logo variant="white" />
            </div>
          </div>
        </div>
      </Section>

      {/* 3. Typography */}
      <Section title="Typography: Plus Jakarta Sans (UI Font)">
        <div className="rounded-[20px] border border-sun/30 bg-white p-6 shadow-sticker-sm space-y-4">
          <div>
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Weight 800 (ExtraBold)</span>
            <p className="text-3xl font-extrabold text-ink">Questions • Quriosity • Quests</p>
          </div>
          <div>
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Weight 700 (Bold)</span>
            <p className="text-2xl font-bold text-ink">Curiosity Learning Platform for Schools</p>
          </div>
          <div>
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Weight 600 (SemiBold)</span>
            <p className="text-lg font-semibold text-ink">Tournament Prelims, Finals & Mixed Bag Rounds</p>
          </div>
          <div>
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Weight 500 (Medium)</span>
            <p className="text-base font-medium text-slate-800">
              Body copy is set in Plus Jakarta Sans Medium and Regular at 15–16px with line-height 1.5+ for effortless reading.
            </p>
          </div>
          <div>
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Weight 400 (Regular)</span>
            <p className="text-sm font-normal text-slate-600">
              Secondary descriptions, timestamps, and metadata tags stay crisp and accessible across desktop and mobile.
            </p>
          </div>
        </div>
      </Section>

      {/* 4. Brand Devices */}
      <Section title="Signature Devices">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="rounded-[20px] border border-sun/30 bg-white p-5 shadow-sticker-sm space-y-3">
            <h3 className="section-bar text-[13px]">Section Divider Bar</h3>
            <p className="text-xs text-slate-600">
              Black uppercase label paired with a full-width Sun-yellow bar (`.section-bar`).
            </p>
          </div>
          <div className="rounded-[20px] border border-sun/30 bg-white p-5 shadow-sticker-sm space-y-3">
            <div className="border-b-2 border-sun pb-2">
              <span className="text-sm font-bold text-ink">Page Title Rule</span>
            </div>
            <p className="text-xs text-slate-600">
              Crisp 2px Sun-yellow border running under primary page headers (`.page-title-rule`).
            </p>
          </div>
        </div>
      </Section>

      {/* 5. Buttons & Badges */}
      <Section title="Buttons & Badges">
        <div className="rounded-[20px] border border-sun/30 bg-white p-6 shadow-sticker-sm space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <Button>Primary (Sun)</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="success">Success (Green)</Button>
            <Button variant="danger">Danger (Red)</Button>
            <Button variant="ghost">Ghost</Button>
            <Button loading>Loading</Button>
            <Button disabled>Disabled</Button>
            <Button size="sm">Small</Button>
            <Button size="lg">Large</Button>
          </div>
          <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-100">
            <Badge tone="sun">Sun Hero</Badge>
            <Badge tone="sky">Sky Blue</Badge>
            <Badge tone="leaf">Green Leaf</Badge>
            <Badge tone="ink">Ink Black</Badge>
            <Badge tone="berry">Error Berry</Badge>
          </div>
        </div>
      </Section>

      {/* 6. Feedback & Interactive Elements */}
      <Section title="Feedback: Toasts, Confirm, Modal, Confetti">
        <div className="flex flex-wrap gap-3">
          <Button variant="success" onClick={() => toast.success('Quiz exported, enjoy!', { celebrate: true })}>
            Success toast + confetti
          </Button>
          <Button variant="danger" onClick={() => toast.error('Upload failed. Check the file and try again.')}>
            Error toast
          </Button>
          <Button variant="secondary" onClick={() => toast.info('QT is warming up the engine.')}>
            Info toast
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              const ok = await confirm({
                title: 'Delete this quiz?',
                message: 'This cannot be undone.',
                danger: true,
              });
              toast.info(ok ? 'Deleted (demo only).' : 'Kept it safe.');
            }}
          >
            Confirm dialog
          </Button>
          <Button onClick={() => setModal(true)}>Modal Demo</Button>
          <span className="relative inline-flex">
            <Button variant="ghost" onClick={() => setBurst((n) => n + 1)}>
              Confetti burst
            </Button>
            {burst > 0 && <Confetti key={burst} />}
          </span>
        </div>
        <Modal
          open={modal}
          onClose={() => setModal(false)}
          title="A friendly modal"
          mascot={<QT pose="professor" height={68} />}
          footer={<Button onClick={() => setModal(false)}>Got it</Button>}
        >
          <p className="text-sm">
            Escape, the close button, and backdrop click all dismiss this modal cleanly.
          </p>
        </Modal>
      </Section>

      {/* 7. Progress */}
      <Section title="Progress Bars">
        <Card className="space-y-6 p-6">
          <Progress value={pct} rider="chase-yellow" label="Upload progress" />
          <Progress value={pct} tone="sky" />
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setPct((p) => Math.max(0, p - 15))}>
              -15%
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPct((p) => Math.min(100, p + 15))}>
              +15%
            </Button>
          </div>
        </Card>
      </Section>

      {/* 8. States */}
      <Section title="Empty & Loading States">
        <div className="grid gap-4 md:grid-cols-3">
          <Card className="relative overflow-hidden">
            <Doodles />
            <div className="relative">
              <EmptyState
                pose="box-yellow"
                title="Nothing here yet"
                hint="Upload a deck and QT will sort the questions."
                action={<Button size="sm">Upload a deck</Button>}
              />
            </div>
          </Card>
          <Card>
            <LoadingState
              pose="quizzing"
              lines={['Reading your brief…', 'Searching the archive…', 'Picking the best ones…']}
            />
          </Card>
          <Card>
            <ErrorState message="We couldn't reach the server." onRetry={() => toast.info('Retrying…')} />
          </Card>
          <Card>
            <EmptyState pose="fishbowl" title="No matches" hint="Try fewer filters; QT is fishing." />
          </Card>
          <Card tone="leaf">
            <EmptyState pose="winner" title="All confirmed!" hint="Every question tag is confirmed." />
          </Card>
          <Card tone="sky">
            <EmptyState pose="sleeping" title="Queue is idle" hint="QT is napping until the next deck." />
          </Card>
        </div>
      </Section>

      {/* 9. All QT Mascot Poses */}
      <Section title={`All ${QT_POSES.length} Curated Poses of QT`}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6">
          {QT_POSES.map((p) => (
            <div
              key={p}
              className="flex flex-col items-center gap-2 rounded-[20px] border border-sun/30 bg-white p-3 shadow-sticker-sm hover:border-sun transition-colors"
            >
              <QT pose={p} height={70} />
              <code className="text-[11px] font-bold text-ink">{p}</code>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
