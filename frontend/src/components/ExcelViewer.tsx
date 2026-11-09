import { useEffect, useRef } from "react";
import "@js-preview/excel/lib/index.css";

/**
 * Spreadsheet viewer backed by @js-preview/excel (runs entirely in the browser
 * on the x-data-spreadsheet grid). The library touches `window`, so it is only
 * ever loaded with a dynamic import() inside an effect.
 *
 * The sheet always draws light, so the host stays white. `onError` lets the
 * caller fall back to another reader if this one can't render the file.
 */
export default function ExcelViewer({
  buffer,
  onError,
}: {
  buffer: ArrayBuffer;
  onError?: (e: unknown) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let previewer: { destroy: () => void } | null = null;

    (async () => {
      try {
        const { default: jsPreviewExcel } = await import("@js-preview/excel");
        if (!alive || !hostRef.current) return;
        const p = jsPreviewExcel.init(hostRef.current, { showContextmenu: false });
        previewer = p;
        // slice(0): the library may take ownership of the buffer, and the
        // caller keeps its copy for remounts.
        await p.preview(buffer.slice(0));
      } catch (e) {
        if (alive) onError?.(e);
      }
    })();

    return () => {
      alive = false;
      try {
        previewer?.destroy();
      } catch {
        /* already torn down */
      }
    };
    // onError is intentionally not a dependency: a new function each render
    // must not rebuild the viewer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer]);

  return <div ref={hostRef} className="h-full min-h-[360px] w-full bg-white" />;
}
