import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

// Pages are lazy-loaded; on a busy machine the first render of a route can take longer than the 1 s default. This changes how long a test WAITS, never what it asserts.
configure({ asyncUtilTimeout: 4000 })

// jsdom does not implement these.
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
Element.prototype.scrollIntoView = vi.fn()
// jsdom has no modal <dialog> support.
HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open') }

afterEach(() => cleanup())
