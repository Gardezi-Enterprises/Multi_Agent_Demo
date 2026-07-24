// The brand mark: a root node branching into four leaves — the system diagram.
// Limbs draw themselves; leaves bloom in; `thinking` makes them twinkle.

export function TreeLogo({ size = 38, thinking = false }: { size?: number; thinking?: boolean }) {
  return (
    <svg
      className={"tree" + (thinking ? " thinking" : "")}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="limb" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--a1)" />
          <stop offset="100%" stopColor="var(--a2)" />
        </linearGradient>
      </defs>
      <path className="limb" d="M24 45V28" />
      <path className="limb" d="M24 28C24 22 15 22 12 16" />
      <path className="limb" d="M24 28C24 22 33 22 36 16" />
      <path className="limb" d="M24 28C24 20 19 18 18 10" />
      <path className="limb" d="M24 28C24 20 29 18 30 10" />
      <circle className="leaf" cx="24" cy="28" r="3.6" fill="url(#limb)" />
      <circle className="leaf" cx="12" cy="16" r="3.2" fill="var(--a2)" />
      <circle className="leaf" cx="18" cy="10" r="3.2" fill="var(--a1)" />
      <circle className="leaf" cx="30" cy="10" r="3.2" fill="var(--a1)" />
      <circle className="leaf" cx="36" cy="16" r="3.2" fill="var(--a2)" />
    </svg>
  );
}

export function Aurora() {
  return (
    <div className="aurora" aria-hidden="true">
      <i />
      <i />
      <i />
    </div>
  );
}
