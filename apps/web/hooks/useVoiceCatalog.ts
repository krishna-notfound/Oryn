"use client";

import { useEffect, useState } from "react";
import { config } from "@/lib/config";
import { getAccessToken } from "@/lib/supabase/client";
import { fetchCatalog, STATIC_CATALOG, type VoiceCatalog } from "@/lib/voices";

/** The server's languages and voices; starts as the built-in copy so pickers render at once. */
export function useVoiceCatalog(): VoiceCatalog {
  const [catalog, setCatalog] = useState<VoiceCatalog>(STATIC_CATALOG);
  useEffect(() => {
    let cancelled = false;
    getAccessToken()
      .then((token) => fetchCatalog(config.backendHttpUrl, token))
      .then((fresh) => {
        if (!cancelled) setCatalog(fresh);
      })
      .catch(() => undefined); // not signed in / offline: keep the built-in copy
    return () => {
      cancelled = true;
    };
  }, []);
  return catalog;
}
