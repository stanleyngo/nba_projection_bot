import styles from "./Message.module.css";

export default function ThinkingIndicator() {
  return (
    <div className={styles.thinking}>
      <span className={styles.ball} />
      Running the numbers…
    </div>
  );
}
