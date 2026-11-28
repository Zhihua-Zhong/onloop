import * as React from "react";
import { hold, release, shown, type ToastClock } from "@/lib/toastTimer";
import { Button } from "@/components/ui/button";

// F's toasts: at most three, bottom right, announced politely. One with an undo follows lib/toastTimer.ts: 9 s, held
// while the pointer is on it or it has focus, and at least 5 s after it is let go. It moves in; it never fades.

interface Item { id: number; text: string; undo?: () => void; planned: number; clock: ToastClock }
type Push = (text: string, undo?: () => void) => void;
const Ctx = React.createContext<Push>(() => undefined);
export const useToast = () => React.useContext(Ctx);

export function Toaster({ children }: { children: React.ReactNode }) {
  const [list, setList] = React.useState<Item[]>([]);
  const timers = React.useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const drop = React.useCallback((id: number) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setList((l) => l.filter((t) => t.id !== id));
  }, []);
  const arm = React.useCallback((id: number, c: ToastClock) => {
    clearTimeout(timers.current.get(id));
    if (c.endsAt !== null) timers.current.set(id, setTimeout(() => drop(id), Math.max(0, c.endsAt - Date.now())));
  }, [drop]);
  const push = React.useCallback<Push>((text, undo) => {
    const now = Date.now();
    const id = now + Math.random();
    const clock = shown(now, Boolean(undo));
    setList((l) => [...l.slice(-2), { id, text, undo, planned: clock.endsAt!, clock }]);
    arm(id, clock);
  }, [arm]);
  const change = (id: number, f: (t: Item) => ToastClock) =>
    setList((l) => l.map((t) => {
      if (t.id !== id) return t;
      const clock = f(t);
      arm(id, clock);
      return { ...t, clock };
    }));
  React.useEffect(() => () => timers.current.forEach(clearTimeout), []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex max-w-[calc(100vw-32px)] flex-col gap-2" role="status" aria-live="polite">
        {list.map((t) => (
          <div
            key={t.id}
            data-testid="toast"
            onMouseEnter={() => change(t.id, (x) => hold(x.clock))}
            onMouseLeave={() => change(t.id, (x) => release(x.clock, Date.now(), x.planned))}
            onFocus={() => change(t.id, (x) => hold(x.clock))}
            onBlur={() => change(t.id, (x) => release(x.clock, Date.now(), x.planned))}
            className="pointer-events-auto flex items-center gap-3 rounded-lg border border-line bg-popover px-3.5 py-2.5 text-sm shadow-xl animate-rise motion-reduce:animate-none"
          >
            <span>{t.text}</span>
            {t.undo && (
              <Button size="sm" variant="outline" onClick={() => { t.undo!(); drop(t.id); }}>
                Undo
              </Button>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
