// Inline SVG line icons (Tabler subset in icon_data.ts) — same set and mapping as the desktop app.
import { ICONS, type IconName } from "./icon_data";

const NS = "http://www.w3.org/2000/svg";

export function icon(name: IconName, size = 20, cls = "ico"): SVGSVGElement {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", cls);
  svg.innerHTML = ICONS[name];
  return svg;
}

export const FAMILY_ICON: Record<string, IconName> = {
  image: "photo", video: "movie", audio: "music", document: "document", model: "cube", archive: "archive",
  subtitle: "subtitle", font: "font", vector: "vector", cad: "cad",
};

// First matching keyword (lower case) in a target group name → icon.
const GROUP_ICON: [string, IconName][] = [
  ["slideshow", "slideshow"], ["frame", "files"], ["one per page", "files"], ["animation", "gif"], ["ocr", "scan"],
  ["pdf", "pdf"], ["split", "scissors"], ["trim", "scissors"], ["image", "photo"], ["video", "movie"], ["audio", "music"],
  ["document", "document"], ["data", "table"], ["3d", "cube"], ["archive", "archive"], ["subtitle", "subtitle"],
  ["font", "font"], ["vector", "vector"], ["cad", "cad"],
];

export function groupIcon(group: string): IconName {
  const g = group.toLowerCase();
  return GROUP_ICON.find(([k]) => g.includes(k))?.[1] ?? "file";
}
