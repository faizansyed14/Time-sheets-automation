import { useEffect, useState } from "react";
import { ReactDocxViewer, parseDocx } from "@extend-ai/react-docx";
import { Minus, Plus } from "lucide-react";
import { Spinner } from "./ui";
import { ServerRenderPane } from "./FilePreview";

type RenderUpload = { filename: string; contentType: string; dataB64: string };

function b64ToBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

// Last fetch only, so reopening the same file does not download it again.
let lastFetch: { url: string; buf: ArrayBuffer } | null = null;

async function loadBytes(url: string): Promise<ArrayBuffer> {
  if (lastFetch && lastFetch.url === url) return lastFetch.buf;
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = await res.arrayBuffer();
  lastFetch = { url, buf };
  return buf;
}

/**
 * .docx preview used everywhere a Word file is shown: @extend-ai/react-docx
 * parses it in the browser and lays it out as real pages.
 *
 * The file is checked with parseDocx first so a bad file fails here, not inside
 * the viewer. Anything that can't be read, or that has no raw bytes to read
 * (only a server render URL), falls back to the previous server page-image
 * renderer, so nothing that previewed before stops previewing.
 */
export default function DocxPane({
  renderUrl,
  renderUpload,
  sourceUrl,
  filename,
  contentType,
}: {
  renderUrl?: string | null;
  renderUpload?: RenderUpload | null;
  sourceUrl?: string | null;
  filename?: string;
  contentType?: string | null;
}) {
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [fallback, setFallback] = useState(false);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    let alive = true;
    setBuffer(null);
    setFallback(false);
    setZoom(1);

    (async () => {
      try {
        // Prefer the bytes we already hold; otherwise fetch the raw file.
        const bytes = renderUpload
          ? b64ToBuffer(renderUpload.dataB64)
          : sourceUrl
            ? await loadBytes(sourceUrl)
            : null;
        if (!bytes) throw new Error("No raw bytes, use the server renderer");
        // slice(0): parsing may consume the buffer, so pass a copy.
        await parseDocx(bytes.slice(0));
        if (alive) setBuffer(bytes);
      } catch (e) {
        if (!alive) return;
        console.warn("DOCX viewer unavailable, using server render:", e);
        setFallback(true);
      }
    })();

    return () => { alive = false; };
  }, [renderUpload, sourceUrl]);

  if (fallback) {
    return (
      <ServerRenderPane
        renderUrl={renderUrl}
        renderUpload={renderUpload}
        sourceUrl={sourceUrl}
        filename={filename}
        contentType={contentType}
      />
    );
  }

  if (!buffer) {
    return <div className="flex h-full items-center justify-center"><Spinner className="h-6 w-6" /></div>;
  }

  const step = (d: number) => setZoom((z) => Math.min(2, Math.max(0.4, Math.round((z + d) * 100) / 100)));

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex shrink-0 items-center justify-end gap-1 border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-500">
        <button type="button" onClick={() => step(-0.1)} aria-label="Zoom out" className="rounded p-1 hover:bg-white hover:text-slate-800">
          <Minus className="h-3.5 w-3.5" />
        </button>
        <span className="w-10 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => step(0.1)} aria-label="Zoom in" className="rounded p-1 hover:bg-white hover:text-slate-800">
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-white">
        <div style={{ zoom }}>
          <ReactDocxViewer file={buffer} emptyState={<Spinner className="m-6 h-6 w-6" />} />
        </div>
      </div>
    </div>
  );
}
