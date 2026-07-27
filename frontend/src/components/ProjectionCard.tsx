import { useEffect, useState } from "react";
import type { Projection } from "../types";
import { statLabel, modelLabel, fmtNum, pct, STAT_ABBR } from "../lib/format";
import styles from "./ProjectionCard.module.css";

const BADGE_CLASS: Record<string, string> = {
  probable: styles.probable,
  questionable: styles.questionable,
  doubtful: styles.doubtful,
  out: styles.out,
};

export default function ProjectionCard({ projection }: { projection: Projection }) {
  const r = projection.result || {};
  const hasLine = typeof projection.line === "number";
  const injury = (r.injury_status || "").toLowerCase();
  const showBadge = injury !== "" && injury !== "healthy";

  const overW = Math.max(0, Math.min(100, (r.prob_over ?? 0) * 100));
  const [fill, setFill] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setFill(overW));
    return () => cancelAnimationFrame(id);
  }, [overW]);

  const header = (
    <div className={styles.top}>
      <div>
        <div className={styles.player}>{projection.player_name}</div>
        <div className={styles.stat}>
          {statLabel(projection.stat)}
          {hasLine && (
            <>
              {" · "}
              <span className={styles.line}>o{fmtNum(projection.line)}</span>
            </>
          )}
        </div>
      </div>
      {showBadge && (
        <span className={`${styles.badge} ${BADGE_CLASS[injury] || ""}`}>{injury}</span>
      )}
    </div>
  );

  // Player OUT — voided, no numbers.
  if (r.available === false) {
    return (
      <div className={styles.card}>
        {header}
        <div className={styles.voided}>
          Listed <b>out</b> — no projection. A prop on a player who doesn't play has no action.
        </div>
      </div>
    );
  }

  const hasProb = typeof r.prob_over === "number";
  const push = r.prob_push ? pct(r.prob_push) : 0;

  return (
    <div className={styles.card}>
      {header}

      <div className={styles.mean}>
        <div className={styles.big}>{fmtNum(r.mean)}</div>
        <div className={styles.lab}>
          <b>projected mean</b>
          {r.median != null ? `median ${fmtNum(r.median)} · ` : ""}
          {modelLabel(r.model)}
        </div>
      </div>

      {hasProb ? (
        <div
          className={styles.meter}
          role="img"
          aria-label={`Over ${pct(r.prob_over)} percent, under ${pct(r.prob_under)} percent`}
        >
          <div className={styles.over} style={{ width: `${fill}%` }}>
            <span className={styles.zlabel}>OVER {pct(r.prob_over)}%</span>
          </div>
          <div className={styles.tick} style={{ left: `${fill}%` }} />
          <div className={styles.underLabel}>
            <span className={styles.zlabelUnder}>UNDER {pct(r.prob_under)}%</span>
          </div>
        </div>
      ) : (
        <div className={styles.foot}>
          <span>no line given — projection only</span>
        </div>
      )}

      {hasProb && push > 0 && (
        <div className={styles.foot}>
          <span>
            push (lands on {fmtNum(projection.line)}): {push}%
          </span>
          <span>line {fmtNum(projection.line)}</span>
        </div>
      )}

      {r.components && (
        <div className={styles.foot}>
          <span className={styles.components}>
            {Object.entries(r.components)
              .map(([k, v]) => `${STAT_ABBR[k.toLowerCase()] || k.toUpperCase()} ${fmtNum(v)}`)
              .join("  ·  ")}
          </span>
        </div>
      )}
    </div>
  );
}
