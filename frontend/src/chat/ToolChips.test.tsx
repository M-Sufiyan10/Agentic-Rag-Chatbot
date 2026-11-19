import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ToolChips from './ToolChips'
import { toolLabel } from './toolLabels'

describe('ToolChips', () => {
  it('labels known and unknown tools', () => {
    expect(toolLabel('web_search', 'running')).toBe('Searching the web…')
    expect(toolLabel('web_search', 'done')).toBe('Searched the web')
    expect(toolLabel('calculator', 'running')).toBe('Calculating…')
    expect(toolLabel('current_datetime', 'done')).toBe('Checked the date and time')
    expect(toolLabel('get_weather', 'running')).toBe('Checking the weather…')
    expect(toolLabel('get_weather', 'done')).toBe('Checked the weather')
    expect(toolLabel('convert_units', 'running')).toBe('Converting units…')
    expect(toolLabel('convert_units', 'done')).toBe('Converted units')
    expect(toolLabel('convert_time', 'running')).toBe('Converting time zones…')
    expect(toolLabel('convert_time', 'done')).toBe('Converted time zones')
    expect(toolLabel('fetch', 'running')).toBe('Reading the web page…')
    expect(toolLabel('fetch', 'done')).toBe('Read the web page')
    expect(toolLabel('list_emails', 'running')).toBe('Checking your inbox…')
    expect(toolLabel('list_emails', 'done')).toBe('Checked your inbox')
    expect(toolLabel('read_email', 'running')).toBe('Reading an email…')
    expect(toolLabel('read_email', 'done')).toBe('Read an email')
    expect(toolLabel('draft_email', 'running')).toBe('Drafting an email…')
    expect(toolLabel('draft_email', 'done')).toBe('Drafted an email')
    expect(toolLabel('weird', 'running')).toBe('Calling weird…')
    expect(toolLabel('weird', 'done')).toBe('Used weird')
  })

  it('shows input and output when a done chip is expanded', async () => {
    render(
      <ToolChips
        tools={[
          {
            id: 'c1',
            name: 'calculator',
            input: { expression: '1+1' },
            output: '2',
            status: 'done',
          },
        ]}
      />,
    )
    await userEvent.click(screen.getByText('Calculated'))
    expect(screen.getByText(/"expression": "1\+1"/)).toBeVisible()
    expect(screen.getByText('Output')).toBeVisible()
    expect(screen.getByText('2')).toBeVisible()
  })

  it('names the running chip by its label', () => {
    render(<ToolChips tools={[{ id: 'c1', name: 'web_search', input: {}, status: 'running' }]} />)
    expect(screen.getByRole('status', { name: 'Searching the web…' })).toBeInTheDocument()
  })

  it('renders history names as done chips', () => {
    render(<ToolChips names={['calculator']} />)
    expect(screen.getByText('Calculated')).toBeVisible()
  })
})
