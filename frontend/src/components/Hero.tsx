import styles from "./Hero.module.css";

const SUGGESTIONS = [
  { k: "over / under", t: "Will Jokić go over 25.5 points tonight?" },
  { k: "combo prop", t: "Luka Dončić PRA over 45.5 tonight?" },
  { k: "availability", t: "Is Giannis playing tonight?" },
  { k: "news", t: "What's the latest on LeBron?" },
];

export default function Hero({ onPick }: { onPick: (text: string) => void }) {
  return (
    <section className={styles.hero}>
      <div className={styles.eyebrow}>NBA prop projections</div>
      <h1 className={styles.h1}>
        Will he go <span className={styles.u}>over?</span>
      </h1>
      <p className={styles.lede}>
        Ask about any player's points, rebounds, or assists — or a combo like PRA. You'll
        get a model-based read on the line, straight from their recent games.
      </p>
      <div className={styles.chips}>
        {SUGGESTIONS.map((s) => (
          <button key={s.t} className={styles.chip} type="button" onClick={() => onPick(s.t)}>
            <span className={styles.k}>{s.k}</span>
            {s.t}
          </button>
        ))}
      </div>
    </section>
  );
}
