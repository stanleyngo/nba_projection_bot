import { type CredentialResponse } from "@react-oauth/google";
import styles from "./Topbar.module.css";

export default function Topbar({
  onNewChat,
  onDeepAnalysis,
  signedIn,
  onSignOut,
}: {
  onNewChat: () => void;
  onDeepAnalysis: () => void;
  signedIn: boolean;
  onSignIn: (credentialResponse: CredentialResponse) => void;
  onSignOut: () => void;
}) {
  return (
    <header className={styles.topbar}>
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true" />
        <span className={styles.name}>nba_projection</span>
        <span className={styles.tag}>prop projection model</span>
      </div>
      {signedIn ? (
        <div className={styles.authArea}>
          <button className={styles.newchat} type="button" onClick={onDeepAnalysis}>
            Deep analysis
          </button>
          <button className={styles.newchat} type="button" onClick={onNewChat}>
            New chat
          </button>
          <button className={styles.newchat} type="button" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      ) : (
        ""
      )}
    </header>
  );
}
