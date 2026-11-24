'use client';

/** Internal brand kit: the visual checklist for the QShala / QT design system. Not linked from the nav. */
import { useState } from 'react';
import {
  QT,
  QT_POSES,
  QTAnim,
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
  { name: 'Sun (primary)', hex: '#FDB913', cls: 'bg-sun' },
  { name: 'Sky (secondary)', hex: '#30B2E7', cls: 'bg-sky' },
  { name: 'Leaf (success)', hex: '#75B543', cls: 'bg-leaf' },
  { name: 'Ink', hex: '#111111', cls: 'bg-ink' },
  { name: 'Cream (page)', hex: '#FFFBF0', cls: 'bg-cream border border-ink/20' },
  { name: 'Berry (danger)', hex: '#E5484D', cls: 'bg-berry' },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="mb-3 text-xl font-extrabold">{title}</h2>
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
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title="QT Brand Kit" subtitle="Colours, type, buttons, states and every pose of QT." pose="professor" />

      <Section title="Logo">
        <div className="flex flex-wrap items-center gap-8 rounded-card border-2 border-ink bg-white p-6 shadow-sticker">
          <Logo tagline />
          <Logo />
          <Logo collapsed />
        </div>
      </Section>

      <Section title="Colour">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {SWATCHES.map((s) => (
            <div key={s.name} className="rounded-card border-2 border-ink bg-white p-2 shadow-sticker-sm">
              <div className={`h-16 rounded-xl ${s.cls}`} />
              <p className="mt-2 text-xs font-bold">{s.name}</p>
              <p className="text-xs text-ink/60">{s.hex}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type: Causten Round">
        <Card className="space-y-1 p-6">
          <p className="text-4xl font-black">Questions • Quriosity • Quests</p>
          <p className="text-2xl font-extrabold">Heading, extra bold</p>
          <p className="text-base font-medium">Body text, medium: the quick brown cat jumps over the lazy quiz.</p>
          <p className="text-sm">Body small, regular: Plain, friendly, readable at length.</p>
        </Card>
      </Section>

      <Section title="Buttons & chips">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="success">Success</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
          <Button loading>Loading</Button>
          <Button disabled>Disabled</Button>
          <Button size="sm">Small</Button>
          <Button size="lg">Large</Button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Badge>Sun</Badge>
          <Badge tone="sky">Sky</Badge>
          <Badge tone="leaf">Leaf</Badge>
          <Badge tone="berry">Berry</Badge>
          <Badge tone="ink">Ink</Badge>
        </div>
      </Section>

      <Section title="Feedback: toasts, confirm, modal, confetti">
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
          <Button onClick={() => setModal(true)}>Modal</Button>
          <span className="relative inline-flex">
            <Button variant="ghost" onClick={() => setBurst((n) => n + 1)}>
              Confetti burst
            </Button>
            {burst > 0 && <Confetti key={burst} />}
          </span>
        </div>
        <Modal open={modal} onClose={() => setModal(false)} title="A friendly modal" mascot={<QT pose="professor" height={68} />}
          footer={<Button onClick={() => setModal(false)}>Got it</Button>}>
          <p className="text-sm">Escape, the close button and a backdrop click all dismiss this. Focus returns to the button that opened it.</p>
        </Modal>
      </Section>

      <Section title="Progress">
        <Card className="space-y-6 p-6">
          <Progress value={pct} rider="chase-yellow" label="Upload progress" />
          <Progress value={pct} tone="sky" />
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setPct((p) => Math.max(0, p - 15))}>-15</Button>
            <Button size="sm" variant="ghost" onClick={() => setPct((p) => Math.min(100, p + 15))}>+15</Button>
          </div>
        </Card>
      </Section>

      <Section title="States">
        <div className="grid gap-4 md:grid-cols-3">
          <Card className="relative overflow-hidden">
            <Doodles />
            <div className="relative">
              <EmptyState pose="box-yellow" title="Nothing here yet" hint="Upload a deck and QT will sort the questions." action={<Button size="sm">Upload a deck</Button>} />
            </div>
          </Card>
          <Card><LoadingState pose="chase-blue" lines={['Reading your brief…', 'Searching the archive…', 'Picking the best ones…']} /></Card>
          <Card><ErrorState message="We couldn't reach the server." onRetry={() => toast.info('Retrying…')} /></Card>
          <Card><EmptyState pose="fishbowl" title="No matches" hint="Try fewer filters; QT is fishing." /></Card>
          <Card tone="leaf"><EmptyState pose="happy" title="All confirmed!" hint="Nothing needs review." /></Card>
          <Card tone="sky"><EmptyState pose="sleepy" title="Queue is idle" hint="QT is napping until the next deck." /></Card>
        </div>
      </Section>

      <Section title="QT animations (use each in its own context)">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
          {([
            ['lets-get-started', 'box-yellow', 'First run / nothing here yet'],
            ['magnify', 'chase-blue', 'Searching or finding questions'],
            ['celebrate', 'professor', 'Something finished well'],
            ['correct-answer', 'happy', 'An answer reveal / correct moment'],
            ['megaphone', 'happy', 'Announcements and batch notices'],
          ] as const).map(([anim, pose, ctx]) => (
            <div key={anim} className="flex flex-col items-center gap-2 rounded-card border-2 border-ink bg-white p-3 text-center shadow-sticker-sm">
              <QTAnim anim={anim} fallbackPose={pose} height={80} />
              <code className="text-[11px] font-bold">{anim}</code>
              <span className="text-[11px] text-ink/70">{ctx}</span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-ink/60">Animated only when the visitor has not asked for reduced motion; otherwise a still pose shows.</p>
      </Section>

      <Section title={`All ${QT_POSES.length} poses of QT`}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6">
          {QT_POSES.map((p) => (
            <div key={p} className="flex flex-col items-center gap-2 rounded-card border-2 border-ink bg-white p-3 shadow-sticker-sm">
              <QT pose={p} height={70} />
              <code className="text-[11px] font-bold">{p}</code>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
