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
 *  page. Uses IntersectionObserver so there are no scroll listeners.
 *
 *  Pass `rootRef` when the list scrolls inside its OWN container (e.g. a panel
 *  with `overflow-y-auto`) instead of the page/viewport — otherwise the sentinel
 *  never intersects the viewport and infinite scroll silently stops working. */
export function useSentinel<T extends HTMLElement = HTMLDivElement>(
  onHit: () => void,
  enabled: boolean,
  /** Scroll container element — pass the mounted node, not a RefObject, so the
   *  observer re-binds when the list panel mounts. Omit for viewport scrolling. */
  scrollRoot?: HTMLElement | null
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
      { root: scrollRoot ?? null, rootMargin: "400px", threshold: 0 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [enabled, scrollRoot]);
  return ref;
}
