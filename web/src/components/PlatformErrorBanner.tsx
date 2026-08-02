import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import { platMeta } from "./ui";

// Maps common error substrings to actionable user-facing guidance
const HINTS: Array<[string, string]> = [
  ["credit balance is too low", "resolved by the Origo team before the next run"],
  ["upgrade or purchase credits", "resolved by the Origo team before the next run"],
  ["quota", "API quota exceeded, resolved by the Origo team"],
  ["rate limit", "too many requests, resolved by the Origo team"],
  ["authentication", "authentication issue, resolved by the Origo team"],
  ["invalid api key", "authentication issue, resolved by the Origo team"],
];

function hint(message: string): string | null {
  const lower = message.toLowerCase();
  for (const [fragment, guidance] of HINTS) {
    if (lower.includes(fragment)) return guidance;
  }
  return null;
}

interface Props {
  errors: Record<string, string>;
}

export function PlatformErrorBanner({ errors }: Props) {
  const entries = Object.entries(errors);
  if (entries.length === 0) return null;

  return (
    <div className="banner warn">
      <span className="bi"><WarningAmberRoundedIcon style={{ fontSize: 16 }} /></span>
      <div>
        <b>
          {entries.length === 1 ? "1 platform failed" : `${entries.length} platforms failed`}, results below are partial
        </b>
        {entries.map(([platform, message]) => {
          const actionHint = hint(message);
          return (
            <div key={platform} className="note">
              <span className="mono">{platMeta(platform).label}</span>: {message}
              {actionHint ? `, ${actionHint}` : ""}
            </div>
          );
        })}
      </div>
    </div>
  );
}
