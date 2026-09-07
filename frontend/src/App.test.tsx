import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('scientific workspace', () => {
  it('renders the hierarchical project context', () => {
    render(<App />)
    expect(screen.getAllByText('T5alphaH Engineering').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('L72M / Q122A').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('Reference structure').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Select variant for validation')).toBeInTheDocument()
  })
})
