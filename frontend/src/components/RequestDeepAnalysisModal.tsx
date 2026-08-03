import { useState } from "react";
import type { FormEvent } from "react";
import Modal from "./Modal";
import styles from "./RequestDeepAnalysisModal.module.css";

export default function RequestDeepAnalysisModal({
  onClose,
  onSubmit,
  pending,
  error,
}: {
  onClose: () => void;
  onSubmit: (playerName: string) => void;
  pending: boolean;
  error: string | null;
}) {
  const [playerName, setPlayerName] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = playerName.trim();
    if (!trimmed || pending) return;
    onSubmit(trimmed);
  }

  return (
    <Modal onClose={onClose} ariaLabel="Request a deep analysis report">
      <h2 className={styles.title}>Deep analysis report</h2>
      <p className={styles.lede}>
        Full season stats, projections, and recent news, synthesized into one report. This runs in
        the background and usually takes a minute or two.
      </p>
      <form onSubmit={handleSubmit}>
        <input
          className={styles.input}
          type="text"
          placeholder="Player name (e.g. Nikola Jokić)"
          value={playerName}
          onChange={(e) => setPlayerName(e.target.value)}
          disabled={pending}
          autoFocus
        />
        {error && <div className={styles.error}>{error}</div>}
        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className={styles.submit} disabled={pending || !playerName.trim()}>
            {pending ? "Starting…" : "Generate report"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
