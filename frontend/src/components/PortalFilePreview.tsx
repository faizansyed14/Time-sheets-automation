import { Modal } from "./ui";
import { SourcePreview } from "./FilePreview";

/** Preview for a portal-uploaded file — reuses the SAME SourcePreview
 *  Compare & Fix already uses for every other source (PDF iframe, XLSX grid,
 *  DOCX/PDF server-rendered page images, plain images), so a portal upload
 *  previews identically everywhere. `renderUrl` is what makes DOCX work: the
 *  browser can't render DOCX natively, so that hits a server page-image
 *  endpoint via plain fetch() with the token in the query string (same
 *  pattern as the internal app's raw-render), never the internal `api`
 *  instance — which is why this works from the portal's own auth namespace
 *  with no extra plumbing. EML never applies here — the upload slots only
 *  accept pdf/docx/xlsx/png/jpg/jpeg. */
export default function PortalFilePreview({
  open,
  onClose,
  url,
  renderUrl,
  filename,
  contentType,
}: {
  open: boolean;
  onClose: () => void;
  url: string;
  renderUrl: string;
  filename: string;
  contentType?: string | null;
}) {
  return (
    <Modal open={open} onClose={onClose} title={filename} wide>
      <div className="h-[75vh]">
        <SourcePreview url={url} renderUrl={renderUrl} name={filename} ct={contentType ?? ""} />
      </div>
    </Modal>
  );
}
