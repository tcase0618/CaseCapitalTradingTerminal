import { useCallback, useEffect, useRef, useState } from "react";
import { displayResource } from "./useDisplayResource";

// Publish each endpoint as it arrives. A slow auxiliary panel cannot hold the
// entire workspace behind a Promise.all loading barrier.
export default function useDisplayBatch(base, sources, defaultInterval = 30000) {
  const [states, setStates] = useState({});
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const subscriptions = sources.map(([path, setter, period]) => {
      const interval = typeof period === "number" || period === false ? period : defaultInterval;
      const resource = displayResource(`${base}${path}`, interval);
      const apply = () => {
        if (!mounted.current) return;
        const state = resource.getSnapshot();
        setStates(previous => previous[path] === state ? previous : { ...previous, [path]: state });
        if (state.data != null) setter(state.data);
      };
      const off = resource.subscribe(apply, interval, 1);
      apply();
      return off;
    });
    return () => { mounted.current = false; subscriptions.forEach(off => off()); };
  }, [base, sources, defaultInterval]);
  const refresh = useCallback((force = true) => Promise.allSettled(sources.map(([path]) => displayResource(`${base}${path}`).refresh(force, 2))), [base, sources]);
  const current = sources.map(([path]) => states[path]);
  return {
    states,
    refresh,
    loading: current.every(state => state?.data == null && !state?.error),
    refreshing: current.some(state => state?.refreshing),
    failed: current.filter(state => state?.error).length,
    updatedAt: current.length && current.every(state => state?.updatedAt) ? Math.min(...current.map(state => state.updatedAt)) : null,
  };
}
