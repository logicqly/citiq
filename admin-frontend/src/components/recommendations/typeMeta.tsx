import ArticleRoundedIcon from "@mui/icons-material/ArticleRounded";
import DataObjectRoundedIcon from "@mui/icons-material/DataObjectRounded";
import SmartToyRoundedIcon from "@mui/icons-material/SmartToyRounded";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import type { SvgIconComponent } from "@mui/icons-material";
import type { RecommendationType } from "../../types";

// Single source of truth for how recommendation types look across the app:
// list page, client tab, badges, and the type-mix chart all read from here.
//
// Hue assignment is fixed per type (never repainted by filters). The hex order
// below is CVD-validated for donut adjacency — blue and violet must not sit
// next to each other.

export const TYPE_ORDER: RecommendationType[] = [
  "content_brief",
  "schema_markup",
  "llms_txt",
  "authority_building",
];

interface TypeMeta {
  label: string;
  blurb: string;
  hex: string;
  badge: string;
  Icon: SvgIconComponent;
}

export const TYPE_META: Record<RecommendationType, TypeMeta> = {
  content_brief: {
    label: "Content brief",
    blurb: "Article outlines targeting uncited queries",
    hex: "#3b82f6",
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    Icon: ArticleRoundedIcon,
  },
  schema_markup: {
    label: "Schema markup",
    blurb: "JSON-LD structured data for key pages",
    hex: "#f59e0b",
    badge: "bg-amber-50 text-amber-700 border-amber-200",
    Icon: DataObjectRoundedIcon,
  },
  llms_txt: {
    label: "llms.txt",
    blurb: "AI crawler guidance file for the site",
    hex: "#7c3aed",
    badge: "bg-violet-50 text-violet-700 border-violet-200",
    Icon: SmartToyRoundedIcon,
  },
  authority_building: {
    label: "Authority",
    blurb: "External citations and mention building",
    hex: "#e11d48",
    badge: "bg-rose-50 text-rose-700 border-rose-200",
    Icon: HubRoundedIcon,
  },
};

export function TypeBadge({ type }: { type: RecommendationType }) {
  const meta = TYPE_META[type];
  if (!meta) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 border border-gray-200 whitespace-nowrap">
        {type}
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap ${meta.badge}`}>
      <meta.Icon style={{ fontSize: 12 }} />
      {meta.label}
    </span>
  );
}
