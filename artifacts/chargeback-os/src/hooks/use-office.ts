import { useQueryClient } from "@tanstack/react-query";
import { useGetOffice, getGetOfficeQueryKey, useActOnOffice } from "@workspace/api-client-react";
import type { OfficeAction } from "@workspace/api-client-react";
import { useToast } from "@/hooks/use-toast";

export function useOffice() {
  return useGetOffice({ query: { queryKey: getGetOfficeQueryKey(), refetchInterval: 4000 } });
}

export function useOfficeAction() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const m = useActOnOffice({
    mutation: {
      onSuccess: (office) => {
        qc.setQueryData(getGetOfficeQueryKey(), office);
        qc.invalidateQueries({ queryKey: getGetOfficeQueryKey() });
      },
      onError: () => toast({ title: "Action failed", description: "The demo server did not accept that action. Try again.", variant: "destructive" }),
    },
  });
  const act = (data: OfficeAction, onDone?: () => void) => m.mutate({ data }, { onSuccess: () => onDone?.() });
  return { act, isPending: m.isPending };
}

export const money = (n: number) => "$" + (n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const pct = (v: number) => Math.max(0, Math.min(100, v));
export const evidenceUrl = (id: string) => `/api/evidence/${encodeURIComponent(id)}`;
export const isOpenTask = (s: string) => !/cancel|done|complete|finished/i.test(s);
