import { create } from "zustand";
import type { ToastVariant } from "../components/ui/toast";

export interface ToastItem {
  id: string;
  title?: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-dismiss after ms; 0 keeps it until closed */
  duration?: number;
}

interface ToastState {
  toasts: ToastItem[];
  push: (t: Omit<ToastItem, "id">) => string;
  dismiss: (id: string) => void;
}

let seq = 0;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = `t-${Date.now()}-${seq++}`;
    set((s) => ({ toasts: [...s.toasts, { id, ...t }] }));
    const duration = t.duration ?? 5000;
    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
      }, duration);
    }
    return id;
  },
  dismiss: (id) =>
    set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));

/** Imperative helper usable outside React components */
export const toast = {
  show(t: Omit<ToastItem, "id">) {
    return useToastStore.getState().push(t);
  },
  dismiss(id: string) {
    useToastStore.getState().dismiss(id);
  },
};
