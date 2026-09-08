import type { ReactNode } from 'react'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="panel-state">{label}</div>
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null
  return <div className="panel-state error">{message}</div>
}

export function Empty({ label }: { label: string }) {
  return <div className="panel-state">{label}</div>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warn' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="content-section">
      <div className="section-heading">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  )
}

export function EnumSelect<T extends string>({
  value,
  options,
  onChange,
  emptyLabel = '—',
}: {
  value: T | ''
  options: readonly T[]
  onChange: (value: T) => void
  emptyLabel?: string
}) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value as T)}>
      {value === '' ? <option value="">{emptyLabel}</option> : null}
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  )
}
