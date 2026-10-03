/** The product mark: a graticuled globe with a parcel on it. */
export default function BrandMark({ size = 28 }) {
  return (
    <svg className="brand__mark" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="brand-ocean" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1ba3d8" />
          <stop offset="1" stopColor="#062338" />
        </linearGradient>
      </defs>
      <circle cx="32" cy="32" r="29" fill="url(#brand-ocean)" stroke="#5fe0e6" strokeWidth="2" />
      <g fill="none" stroke="#5fe0e6" strokeWidth="1.6" opacity="0.85">
        <ellipse cx="32" cy="32" rx="12" ry="29" />
        <path d="M4 32h56M9 18h46M9 46h46" />
      </g>
      <path
        d="M24 26l16-4 4 14-14 6z"
        fill="#eaf6fb"
        fillOpacity="0.2"
        stroke="#eaf6fb"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}
