import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { fetchSystemNotice } from "../api/client";
import { Button, Modal } from "./ui";

const DISMISS_KEY = "system_notice_dismissed";

/** Admin-authored popup shown to every logged-in role (including viewer and
 *  vault_matcher) whenever the notice is enabled. Re-shows automatically if
 *  admin changes the message or re-enables it after a dismiss — the dismiss
 *  is remembered per exact message, not just "seen once ever". */
export default function SystemNoticeBanner() {
  const { data: notice } = useQuery({
    queryKey: ["system-notice"],
    queryFn: fetchSystemNotice,
    refetchInterval: 60_000,
  });
  const [dismissed, setDismissed] = useState<string | null>(() => {
    try { return localStorage.getItem(DISMISS_KEY); } catch { return null; }
  });

  if (!notice || !notice.enabled || !notice.message.trim()) return null;
  const key = `${notice.updated_at ?? ""}::${notice.message}`;
  if (dismissed === key) return null;

  const dismiss = () => {
    try { localStorage.setItem(DISMISS_KEY, key); } catch { /* private browsing, etc. */ }
    setDismissed(key);
  };

  return (
    <Modal open onClose={dismiss} title="Notice">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-50 text-amber-600 ring-1 ring-inset ring-amber-200">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <p className="pt-1.5 text-sm leading-relaxed text-slate-700">{notice.message}</p>
      </div>
      <div className="mt-5 flex justify-end">
        <Button onClick={dismiss}>Got it</Button>
      </div>
    </Modal>
  );
}
