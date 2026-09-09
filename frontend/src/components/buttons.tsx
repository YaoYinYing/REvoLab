import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Button({
  kind = 'primary',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { kind?: 'primary' | 'quiet' | 'danger' }) {
  return (
    <button className={`btn btn-${kind}`} {...props}>
      {props.children as ReactNode}
    </button>
  )
}
