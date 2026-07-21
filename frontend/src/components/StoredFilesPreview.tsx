import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { fileContentUrl, fileRenderUrl, recordSources } from "../api/client";
import { formatBytes } from "../lib/utils";
import type { PreviewFile } from "../lib/filePreview";
import { FilePreviewModal, PreviewableFileRow } from "./FilePreview";

/** Previewable files saved in the record's month folder (e.g. extracted PDF from .eml). */
export default function StoredFilesPreview({ recordId }: { recordId: string }) {
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const { data: sources } = useQuery({
    queryKey: ["record-sources", recordId],
    queryFn: () => recordSources(recordId),
  });

  const files = (sources ?? []).filter((s) => s.name !== "extraction_result.json");

  if (!files.length) return null;

  return (
    <>
      <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3">
        <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Stored files</p>
        <div className="space-y-1.5">
          {files.map((s) => {
            const file: PreviewFile = {
              url: fileContentUrl(s.rel_path),
              filename: s.name,
              contentType: s.content_type,
              renderUrl: fileRenderUrl(s.rel_path),
            };
            return (
              <PreviewableFileRow
                key={s.rel_path}
                file={file}
                onPreview={setPreview}
                icon={<FileText className="h-4 w-4 shrink-0 text-slate-400" />}
                meta={<span className="text-[11px] text-slate-400">{formatBytes(s.size)}</span>}
                className="border-transparent px-2 py-1.5 hover:border-transparent"
              />
            );
          })}
        </div>
      </div>
      <FilePreviewModal file={preview} onClose={() => setPreview(null)} />
    </>
  );
}
