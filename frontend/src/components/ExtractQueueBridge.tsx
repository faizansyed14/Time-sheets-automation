import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { extractQueueStore } from "../lib/extractQueue";
import { useToast } from "./toast";

/** Wires the module-level extract queue store up to React (query client,
 * toasts, navigation) once. Mount near the app root — the store itself
 * lives outside React so it survives page navigation. */
export default function ExtractQueueBridge() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    extractQueueStore.init(qc, toast, (path) => navigate(path));
  }, [qc, toast, navigate]);

  return null;
}
