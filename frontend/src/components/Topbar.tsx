import styles from "./Topbar.module.css";

export default function Topbar({ onNewChat }: { onNewChat: () => void }) {
  return (
    <header className={styles.topbar}>
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true" />
        <span className={styles.name}>nba_projection</span>
        <span className={styles.tag}>prop projection model</span>
      </div>
      <button className={styles.newchat} type="button" onClick={onNewChat}>
        New chat
      </button>
    </header>
  );
}
