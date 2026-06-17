import { useEffect, useRef, useState } from "react";

/** Debounce a fast-changing value (e.g. a search box) so we only hit the
 *  server-side search after the user pauses typing. */
export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

/** Returns a ref to attach to a sentinel <div> at the bottom of a scroll list.
 *  When it scrolls into view (and `enabled`), `onHit` fires to load the next
 *  page. Uses IntersectionObserver so there are no scroll listeners. */
export function useSentinel<T extends HTMLElement = HTMLDivElement>(
  onHit: () => void,
  enabled: boolean
) {
  const ref = useRef<T | null>(null);
  const cb = useRef(onHit);
  cb.current = onHit;
  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) cb.current();
      },
      { rootMargin: "400px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [enabled]);
  return ref;
}
