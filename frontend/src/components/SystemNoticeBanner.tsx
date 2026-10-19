import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { ackSystemNotice, fetchSystemNotice, type SystemNotice } from "../api/client";
import { Button, Modal } from "./ui";

/** Admin-authored popup shown to every logged-in role (including viewer and
 *  vault_matcher) while the notice is enabled. It has ONE button, "OK": the
 *  click is saved on the server against the signed-in user, so the popup never
 *  comes back for them (on any browser or device) and the admin sees their
 *  thumbs-up. It can't be dismissed any other way. A changed message — or the
 *  notice being switched back on — clears everyone's OK and shows it again. */
export default function SystemNoticeBanner() {
  const qc = useQueryClient();
  const { data: notice } = useQuery({
    queryKey: ["system-notice"],
    queryFn: fetchSystemNotice,
    refetchInterval: 60_000,
  });

  const ack = useMutation({
    mutationFn: (message: string) => ackSystemNotice(message),
    // Hide it straight away; the server's answer (or a refetch on failure) is
    // the source of truth.
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: ["system-notice"] });
      qc.setQueryData<SystemNotice>(["system-notice"], (old) => (old ? { ...old, acknowledged: true } : old));
    },
    onSuccess: (fresh) => qc.setQueryData(["system-notice"], fresh),
    onError: () => qc.invalidateQueries({ queryKey: ["system-notice"] }),
  });

  if (!notice || !notice.enabled || !notice.message.trim() || notice.acknowledged) return null;

  return (
    <Modal open onClose={() => {}} dismissible={false} title="Notice">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-50 text-amber-600 ring-1 ring-inset ring-amber-200">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <p className="pt-1.5 text-sm leading-relaxed text-slate-700">{notice.message}</p>
      </div>
      <div className="mt-5 flex justify-end">
        <Button disabled={ack.isPending} onClick={() => ack.mutate(notice.message)}>OK</Button>
      </div>
    </Modal>
  );
}
