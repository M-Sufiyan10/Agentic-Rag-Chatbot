import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Citations from './Citations'

const items = [
  { document_id: 'd1', filename: 'guide.pdf', chunk_index: 0, snippet: 'First snippet text' },
  { document_id: 'd1', filename: 'guide.pdf', chunk_index: 4, snippet: 'Second snippet text' },
]

describe('Citations', () => {
  it('renders nothing for no items', () => {
    const { container } = render(<Citations items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists sources with 1-based chunk numbers and expandable snippets', async () => {
    render(<Citations items={items} />)
    expect(screen.getByText('Sources')).toBeVisible()
    expect(screen.getByText('· chunk 1')).toBeVisible()
    expect(screen.getByText('· chunk 5')).toBeVisible()
    expect(screen.getByText('First snippet text')).not.toBeVisible()
    await userEvent.click(screen.getAllByText('guide.pdf')[0])
    expect(screen.getByText('First snippet text')).toBeVisible()
  })
})
