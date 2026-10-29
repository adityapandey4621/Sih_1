/** Client-side checks for fast feedback only. The backend validates everything again. */

export const PASSWORD_MIN_LENGTH = 12

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export const isEmail = (v: string) => EMAIL.test(v.trim())

export type Errors<K extends string> = Partial<Record<K, string>>
