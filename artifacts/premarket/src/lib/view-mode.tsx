import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ViewMode = "desktop" | "mobile";

const STORAGE_KEY = "viewMode";

function isNarrowScreen(): boolean {
  if (typeof window === "undefined") return false;
  return window.innerWidth < 768;
}

function readStored(): ViewMode | null {
  if (typeof window === "undefined") return null;
  const saved = window.localStorage.getItem(STORAGE_KEY);
  return saved === "mobile" || saved === "desktop" ? saved : null;
}

interface ViewModeContextValue {
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  toggleViewMode: () => void;
}

const ViewModeContext = createContext<ViewModeContextValue | null>(null);

export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [viewMode, setViewModeState] = useState<ViewMode>(() => {
    if (isNarrowScreen()) return "mobile";
    return readStored() ?? "desktop";
  });

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, viewMode);
    document.documentElement.classList.toggle("mobile-mode", viewMode === "mobile");
  }, [viewMode]);

  useEffect(() => {
    const handleResize = () => {
      if (isNarrowScreen()) setViewModeState("mobile");
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const setViewMode = (mode: ViewMode) => setViewModeState(mode);
  const toggleViewMode = () => setViewModeState((m) => (m === "mobile" ? "desktop" : "mobile"));

  return (
    <ViewModeContext.Provider value={{ viewMode, setViewMode, toggleViewMode }}>
      {children}
    </ViewModeContext.Provider>
  );
}

export function useViewMode(): ViewModeContextValue {
  const ctx = useContext(ViewModeContext);
  if (!ctx) throw new Error("useViewMode deve ser usado dentro de ViewModeProvider");
  return ctx;
}
