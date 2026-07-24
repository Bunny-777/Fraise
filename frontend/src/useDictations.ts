import { useEffect, useState } from "react";
import { httpBase } from "./useVoiceAgent";

export interface DictationSummary {
  id: string;
  snippet: string;
  is_email: boolean;
  status: string;
  created_at: string;
}

export function useDictationHistory(sid: string, refreshKey: string | undefined) {
  const [dictations, setDictations] = useState<DictationSummary[]>([]);

  useEffect(() => {
    if (!sid) return;
    let stale = false;
    const url = new URL("/dictations", httpBase());
    url.searchParams.set("sid", sid);
    fetch(url)
      .then((res) => (res.ok ? res.json() : []))
      .then((list) => {
        if (!stale) setDictations(list);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [sid, refreshKey]);

  return dictations;
}
