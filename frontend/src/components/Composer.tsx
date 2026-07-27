import { useEffect, useRef } from "react";
import styles from "./Composer.module.css";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}

export default function Composer({ value, onChange, onSubmit, disabled }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Autosize the textarea to its content, capped at 140px.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }, [value]);

  const canSend = !disabled && value.trim() !== "";

  return (
    <div className={styles.wrap}>
      <div className={styles.composer}>
        <div className={styles.inputrow}>
          <textarea
            ref={ref}
            className={styles.q}
            rows={1}
            placeholder="Ask about a player's line…"
            autoComplete="off"
            autoFocus
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
          />
          <button
            className={styles.send}
            type="button"
            aria-label="Send"
            disabled={!canSend}
            onClick={() => onSubmit()}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M7 11l5-5 5 5M12 6v13" />
            </svg>
          </button>
        </div>
        <div className={styles.disclaimer}>
          Statistical model from recent games · not betting advice
        </div>
      </div>
    </div>
  );
}
