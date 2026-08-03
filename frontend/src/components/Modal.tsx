import { useEffect } from "react";
import type { ReactNode } from "react";
import styles from "./Modal.module.css";

/** Minimal modal shell shared by RequestDeepAnalysisModal and DeepAnalysisReportModal:
 * a backdrop overlay, Escape-to-close, click-outside-to-close, and an explicit close
 * button. `size="lg"` is for content that needs real room to scroll (the report body);
 * the default "sm" fits a small form. */
export default function Modal({
  onClose,
  ariaLabel,
  children,
  size = "sm",
}: {
  onClose: () => void;
  ariaLabel: string;
  children: ReactNode;
  size?: "sm" | "lg";
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div
        className={`${styles.panel} ${size === "lg" ? styles.panelLarge : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        onClick={(e) => e.stopPropagation()}
      >
        <button className={styles.closeButton} type="button" onClick={onClose} aria-label="Close">
          ×
        </button>
        {children}
      </div>
    </div>
  );
}
