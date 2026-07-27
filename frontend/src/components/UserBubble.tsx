import styles from "./Message.module.css";

export default function UserBubble({ text }: { text: string }) {
  return (
    <div className={styles.msgUser}>
      <div className={styles.bubbleUser}>{text}</div>
    </div>
  );
}
