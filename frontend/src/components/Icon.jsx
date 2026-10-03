// A small set of line icons drawn for this project (24 px grid, 1.75 px
// stroke). Kept local so the interface has one consistent icon voice.

const PATHS = {
  map: "M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5zM9 4v13.5M15 6.5V20",
  layers: "M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  globe: "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.6 2.4 4 5.6 4 9s-1.4 6.6-4 9c-2.6-2.4-4-5.6-4-9s1.4-6.6 4-9z",
  check: "M4.5 12.5l5 5L19.5 7",
  x: "M6 6l12 12M18 6L6 18",
  flag: "M5 21V4M5 4.5h12l-2.5 4 2.5 4H5",
  pencil: "M4 20l1-4.5L16.5 4l3.5 3.5L8.5 19zM14 6.5l3.5 3.5",
  crosshair: "M12 3v4M12 17v4M3 12h4M17 12h4M12 8a4 4 0 100 8 4 4 0 000-8z",
  alert: "M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.3v.2",
  info: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7.6v.2",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v4h16v-4",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  eye: "M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zM12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z",
  "eye-off": "M3 3l18 18M10.6 5.6c.45-.07.92-.1 1.4-.1 6 0 9.5 6.5 9.5 6.5a16 16 0 01-3 3.7M6.2 7.2A16 16 0 002.5 12s3.5 6.5 9.5 6.5c1.5 0 2.8-.4 4-1M9.9 9.9a3 3 0 004.2 4.2",
  search: "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM15.5 15.5L20 20",
  "chevron-right": "M9 5l7 7-7 7",
  "chevron-down": "M5 9l7 7 7-7",
  "arrow-left": "M19 12H5M11 6l-6 6 6 6",
  building: "M5 21V5l8-2v18M13 9l6 2v10M3 21h18M8 8h2M8 12h2M8 16h2M16 14h1M16 17.5h1",
  road: "M8 3L4 21M16 3l4 18M12 4v3M12 10.5v3M12 17v3",
  droplet: "M12 3.5s6 6.2 6 10.5a6 6 0 01-12 0c0-4.3 6-10.5 6-10.5z",
  sprout: "M12 21v-9M12 12c0-3.5-2.5-6-6.5-6 0 4 2.5 6 6.5 6zM12 14c0-3 2-5 6-5 0 3.5-2.5 5-6 5z",
  shapes: "M4 4h7v7H4zM17.5 13.5l3.5 6.5h-7zM7.5 14a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM16.5 4.5a3 3 0 100 6 3 3 0 000-6z",
  parcel: "M4 7l8-3 8 3v10l-8 3-8-3zM4 7l8 3 8-3M12 10v10",
  box: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
  user: "M12 4a4 4 0 100 8 4 4 0 000-8zM4.5 20.5c.8-4 3.8-6 7.5-6s6.700 2 7.5 6",
  logout: "M10 4H5v16h5M14 8l4 4-4 4M18 12H9",
  refresh: "M20 11a8 8 0 00-14.3-4.5L4 8M4 4v4h4M4 13a8 8 0 0014.3 4.5L20 16M20 20v-4h-4",
  clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7.5V12l3 2",
  cpu: "M7 7h10v10H7zM10 10h4v4h-4zM9.5 3v4M14.5 3v4M9.5 17v4M14.5 17v4M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4",
  database: "M4 6.5C4 4.6 7.6 3 12 3s8 1.6 8 3.5S16.400 10 12 10 4 8.400 4 6.5zM4 6.5v11C4 19.400 7.600 21 12 21s8-1.600 8-3.500v-11M4 12c0 1.900 3.600 3.500 8 3.500s8-1.600 8-3.500",
  file: "M6 3h8l5 5v13H6zM14 3v5h5M9 13h6M9 17h6",
  shield: "M12 3l8 3v6c0 4.500-3.200 7.800-8 9-4.800-1.200-8-4.500-8-9V6zM8.500 12l2.500 2.500 4.500-5",
  chart: "M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-7",
  mountain: "M3 20l6.500-11 4 6.500 2.500-4L21 20zM9.500 9l1.800 3",
  history: "M4 12a8 8 0 108-8 8 8 0 00-6.500 3.300M4 4v4h4M12 8v4.500l3 1.800",
  lock: "M6 11h12v10H6zM8.500 11V8a3.500 3.500 0 017 0v3",
  mail: "M3 6h18v12H3zM3 7l9 6.500L21 7",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  target: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 8a4 4 0 100 8 4 4 0 000-8zM12 11.800v.400",
  satellite: "M5 14l5 5M3.500 9.500l5-5 4.500 4.500-5 5zM13 11l4.500 4.500-5 5L8 16M14 4a6 6 0 016 6M14 7.500a2.500 2.500 0 012.500 2.500",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M14 4.500v5M8 14.500v5",
  external: "M14 4h6v6M20 4l-9 9M18 13.500V20H4V6h6.500",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.200M4 12h.200M4 18h.200",
  "zoom-fit": "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5M9 9h6v6H9z",
  undo: "M9 7L4 12l5 5M4 12h10a6 6 0 010 12h-2",
};

export default function Icon({ name, size = 18, className = "", title, strokeWidth = 1.75 }) {
  const d = PATHS[name] || PATHS.info;
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      <path d={d} />
    </svg>
  );
}

export const ICON_NAMES = Object.keys(PATHS);
