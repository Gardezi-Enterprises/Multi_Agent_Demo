import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type Kind = "info" | "ok" | "err";
interface Toast {
  id: number;
  message: string;
  kind: Kind;
  gone?: boolean;
}

const ToastCtx = createContext<(message: string, kind?: Kind) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const push = useCallback((message: string, kind: Kind = "info") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => {
      setToasts((t) => t.map((x) => (x.id === id ? { ...x, gone: true } : x)));
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 200);
    }, 4200);
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="assertive">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}${t.gone ? " gone" : ""}`}>
            <i />
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
