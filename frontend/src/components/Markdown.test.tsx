import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from './Markdown'

describe('safe Markdown renderer', () => {
  it('renders a bounded Markdown subset as React elements', () => {
    render(
      <Markdown
        source={'# Heading\n\n**bold** and *italic* and `code`\n\n- one\n- two\n\n[link](https://example.com)'}
      />,
    )
    expect(screen.getByText('Heading')).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByText('italic').tagName).toBe('EM')
    expect(screen.getByText('code').tagName).toBe('CODE')
    expect(screen.getByText('one').tagName).toBe('LI')
    const link = screen.getByText('link')
    expect(link.tagName).toBe('A')
    expect(link).toHaveAttribute('href', 'https://example.com')
  })

  it('never emits active HTML: script tags stay inert text', () => {
    const { container } = render(
      <Markdown source={'<script>alert(1)</script>\n\n<b>not bold</b>'} />,
    )
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
    expect(screen.getByText('<b>not bold</b>')).toBeInTheDocument()
  })

  it('refuses unsafe link schemes', () => {
    const { container } = render(
      <Markdown source={'[click](javascript:alert(1)) [data](data:text/html,x)'} />,
    )
    expect(container.querySelector('a')).toBeNull()
    expect(screen.getByText(/javascript:alert\(1\)/)).toBeInTheDocument()
  })

  it('renders fenced code blocks without executing their content', () => {
    const { container } = render(<Markdown source={'```\n<script>alert(2)</script>\n```'} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('pre code')?.textContent).toBe('<script>alert(2)</script>')
  })
})
