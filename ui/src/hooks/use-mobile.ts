import * as React from "react";

export const MOBILE_BREAKPOINT = 768;

function getIsMobile() {
  if (typeof window === "undefined") return false;
  return window.innerWidth < MOBILE_BREAKPOINT;
}

export function useIsMobile() {
  const [state, setState] = React.useState<{ isMobile: boolean; isReady: boolean }>(() => ({
    isMobile: false,
    isReady: false,
  }));

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => {
      setState({ isMobile: getIsMobile(), isReady: true });
    };
    mql.addEventListener("change", onChange);
    setState({ isMobile: getIsMobile(), isReady: true });
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return state;
}
