import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom does not implement these.
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
Element.prototype.scrollIntoView = vi.fn()
// jsdom has no modal <dialog> support.
HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open') }

afterEach(() => cleanup())
