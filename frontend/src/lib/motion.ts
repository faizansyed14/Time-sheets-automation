import type { Variants } from "framer-motion";

/** The only motion presets in the product, so everything moves the same way. */
export const ease = [0.22, 1, 0.36, 1] as const;

export const spring = {
  soft: { type: "spring", stiffness: 340, damping: 34, mass: 0.9 }, // panels, dialogs
  snappy: { type: "spring", stiffness: 520, damping: 38, mass: 0.8 }, // tabs, switches, indicators
  layout: { type: "spring", stiffness: 380, damping: 40, mass: 0.9 }, // reflowing lists, expanding cards
} as const;

export const duration = { fast: 0.16, base: 0.24, slow: 0.4 } as const;

export const rise: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: duration.slow, ease } },
  exit: { opacity: 0, y: 4, transition: { duration: duration.fast, ease } },
};

export const fade: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: duration.base, ease } },
  exit: { opacity: 0, transition: { duration: duration.fast, ease } },
};

export const pop: Variants = {
  hidden: { opacity: 0, scale: 0.97, y: 4 },
  show: { opacity: 1, scale: 1, y: 0, transition: { duration: duration.base, ease } },
  exit: { opacity: 0, scale: 0.97, y: 4, transition: { duration: duration.fast, ease } },
};

export const stagger = (gap = 0.045, start = 0.04): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: start } },
});

/** Cap per-item delays so long lists never feel slow. */
export const STAGGER_CAP = 14;
export const cappedDelay = (index: number, gap = 0.045) => Math.min(index, STAGGER_CAP) * gap;
