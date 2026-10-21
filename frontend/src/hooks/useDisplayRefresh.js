import { useEffect, useRef } from "react";

export const DISPLAY_REFRESH_EVENT = "case-capital:display-refresh";

// Legacy read-only pages opt in without remounting or repeating commands.
export default function useDisplayRefresh(refresh) {
  const current = useRef(refresh);
  current.current = refresh;
  useEffect(() => {
    const update = () => current.current?.();
    window.addEventListener(DISPLAY_REFRESH_EVENT, update);
    return () => window.removeEventListener(DISPLAY_REFRESH_EVENT, update);
  }, []);
}
