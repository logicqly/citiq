import { DISPLAY_FIELDS, type DisplayConfig } from "./displayFields";

// The list of client-display toggles, shared by the global-defaults panel and
// the per-client override panel. Sub-fields (model IDs, raw responses, run IDs)
// render indented under the field they belong to.
export function DisplayChecklist({
  config,
  onToggle,
  disabled = false,
}: {
  config: DisplayConfig;
  onToggle: (key: string) => void;
  disabled?: boolean;
}) {
  return (
    <>
      {DISPLAY_FIELDS.map((f) => (
        <label
          key={f.key}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            fontSize: 12.5,
            color: f.sub ? "var(--ink4)" : "var(--ink2)",
            padding: "5px 0",
            paddingLeft: f.sub ? 18 : 0,
            cursor: disabled ? "not-allowed" : "pointer",
            opacity: disabled ? 0.5 : 1,
          }}
        >
          <input
            type="checkbox"
            checked={!!config[f.key]}
            disabled={disabled}
            onChange={() => onToggle(f.key)}
            style={{ accentColor: "var(--white)", cursor: disabled ? "not-allowed" : "pointer" }}
          />
          <span>{f.label}</span>
        </label>
      ))}
    </>
  );
}
