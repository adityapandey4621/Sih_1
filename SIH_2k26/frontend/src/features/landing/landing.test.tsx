import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DASHBOARDS } from '../dashboard/dashboardSpecs'
import { NAV_ITEMS, homePathFor, visibleNav } from '../shell/navigation'
import { ROUTE_STAGES } from './landing.content'
import { ANON, mockApi, renderApp } from '../../test/helpers'

afterEach(() => vi.unstubAllGlobals())

describe('landing page', () => {
  it('shows the hero, both calls to action and an honest illustrative label', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: /Every application\. Every action\. Accounted for\./ })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Sign in' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Explore the workflow' })).toHaveAttribute('href', '#workflow')
    expect(screen.getByText(/not live data, not connected to any AICTE system/)).toBeInTheDocument()
    expect(screen.getAllByText(/not an official AICTE/i).length).toBeGreaterThan(0)
  })

  it('lets the reader select a stage and read what happens there', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/')
    const scrutiny = await screen.findByRole('button', { name: /Scrutiny/ })
    await userEvent.click(scrutiny)
    expect(scrutiny).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText(/Stage 3 of 6/)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(ROUTE_STAGES[2]!.summary.slice(0, 30)))).toBeInTheDocument()
  })

  it('marks the committee stage as conditional', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/')
    const btn = await screen.findByRole('button', { name: /Committee processes/ })
    expect(within(btn).getByText(/where applicable/)).toBeInTheDocument()
  })
})

describe('navigation and dashboards (no business rules in the UI)', () => {
  it('has exactly one dashboard per permission and each is reachable from navigation', () => {
    const perms = DASHBOARDS.map((d) => d.permission)
    expect(new Set(perms).size).toBe(perms.length)
    for (const d of DASHBOARDS) expect(NAV_ITEMS.some((n) => n.permission === d.permission)).toBe(true)
  })

  it('only shows items the permission list allows', () => {
    expect(visibleNav(['dashboard:rules']).map((i) => i.id)).toEqual(['rules'])
    expect(visibleNav([])).toEqual([])
    expect(homePathFor(['audit:read'])).toBeNull()
    expect(homePathFor(['audit:read', 'dashboard:audit'])).toBe('/app/audit')
  })

  it('dashboard specs contain no numbers that could pass as rules or statistics', () => {
    const text = JSON.stringify(DASHBOARDS.map(({ icon: _icon, ...rest }) => rest))
    expect(text).not.toMatch(/\d+\s*(days?|hours?|%)/i)
  })
})
