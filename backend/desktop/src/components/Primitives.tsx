import { LoaderCircle, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

export function AppButton({
  children,
  icon: Icon,
  tone = "secondary",
  size = "default",
  loading = false,
  ...props
}: {
  children?: ReactNode
  icon?: LucideIcon
  tone?: "primary" | "secondary" | "ghost" | "danger"
  size?: "default" | "small" | "icon"
  loading?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const CurrentIcon = loading ? LoaderCircle : Icon
  return (
    <button className={`button button-${tone} button-${size}`} {...props} disabled={props.disabled || loading}>
      {CurrentIcon ? <CurrentIcon size={size === "small" ? 14 : 16} className={loading ? "spin" : ""} aria-hidden="true" /> : null}
      {children ? <span>{children}</span> : null}
    </button>
  )
}

export function StatusBadge({ tone, children }: { tone: "neutral" | "success" | "warning" | "danger" | "blue"; children: ReactNode }) {
  return <span className={`status-badge status-${tone}`}><span className="status-dot" />{children}</span>
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (value: T) => void; options: { value: T; label: string; icon?: LucideIcon }[] }) {
  return (
    <div className="segmented">
      {options.map((option) => {
        const Icon = option.icon
        return (
          <button key={option.value} aria-pressed={value === option.value} className={value === option.value ? "active" : ""} onClick={() => onChange(option.value)}>
            {Icon ? <Icon size={14} /> : null}{option.label}
          </button>
        )
      })}
    </div>
  )
}
