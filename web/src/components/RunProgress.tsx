import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import type { RunRead } from "../lib/types";
import { BarMeter, RunStatusChip } from "./ui";

type Phase = "collect" | "analyze" | "recommend";

// The progress bar counts MONITORING calls only. Once it reads N/N the run is
// still working through analysis and recommendations — the stepper names the
// phase so a full bar + "running" doesn't look stuck.
function livePhase(run: RunRead): Phase {
  if (run.completed_prompts < run.total_prompts) return "collect";
  if (run.generation_status === "running") return "recommend";
  return "analyze";
}

const PHASE_LABEL: Record<Phase, string> = {
  collect: "Collecting AI responses",
  analyze: "Analyzing responses",
  recommend: "Generating recommendations",
};

export function RunProgress({ run }: { run: RunRead }) {
  const pct = run.total_prompts > 0 ? Math.round((run.completed_prompts / run.total_prompts) * 100) : 0;
  const phase = livePhase(run);
  const stepC = phase === "collect" ? "now" : "done";
  const stepA = phase === "analyze" ? "now" : phase === "recommend" ? "done" : "";
  const stepR = phase === "recommend" ? "now" : "";

  return (
    <div className="banner live">
      <span className="bi" style={{ color: "var(--white)" }}>
        <PlayArrowRoundedIcon style={{ fontSize: 16 }} />
      </span>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <b>Run in progress</b>
          <RunStatusChip status={run.status} />
        </div>
        {run.status === "running" && (
          <div className="stepper" style={{ marginTop: 9 }}>
            <div className={`step ${stepC}`}><span className="sd">1</span><span className="sl">Collect</span></div>
            <div className={`step-line ${stepC === "done" ? "done" : ""}`} />
            <div className={`step ${stepA}`}><span className="sd">2</span><span className="sl">Analyze</span></div>
            <div className={`step-line ${stepR ? "done" : ""}`} />
            <div className={`step ${stepR}`}><span className="sd">3</span><span className="sl">Recommend</span></div>
            <span style={{ marginLeft: 16, fontSize: 12, color: "var(--ink3)" }}>{PHASE_LABEL[phase]}</span>
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 9 }}>
          <BarMeter pct={pct} width={360} />
          <span className="mono" style={{ fontSize: 11.5 }}>{run.completed_prompts}/{run.total_prompts}</span>
          <span className="mono dim" style={{ fontSize: 11.5 }}>{pct}%</span>
        </div>
        {run.error_message && (
          <div className="note" style={{ color: "var(--bad)", marginTop: 8 }}>{run.error_message}</div>
        )}
      </div>
    </div>
  );
}
