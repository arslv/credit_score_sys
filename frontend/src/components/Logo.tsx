import { useId } from "react";

// T23: знак DIALOG Scoring — буква «D» с галочкой внутри («решение принято»).
// Свой SVG, а не иконка из набора: одинаково чёткий на любом размере.
// variant="color" — градиентная плитка (меню), "light" — белая плитка
// с градиентным знаком (тёмный/цветной фон экрана входа).
export function LogoMark({ size = 28, variant = "color" }: { size?: number; variant?: "color" | "light" }) {
  const gradientId = useId();
  const tile = variant === "color" ? `url(#${gradientId})` : "#ffffff";
  const ink = variant === "color" ? "#ffffff" : `url(#${gradientId})`;
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2f5bff" />
          <stop offset="1" stopColor="#7a4cf0" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={tile} />
      <path
        d="M10.5 8.5h5.25a7.5 7.5 0 0 1 0 15H10.5z"
        fill="none"
        stroke={ink}
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <path
        d="M13.4 16.1l2.2 2.2 3.9-4.3"
        fill="none"
        stroke={ink}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ variant = "color" }: { variant?: "color" | "light" }) {
  return (
    <span className={`logo logo--${variant}`}>
      <LogoMark variant={variant} />
      <span className="logo__text">
        DIALOG <span className="logo__accent">Scoring</span>
      </span>
    </span>
  );
}
