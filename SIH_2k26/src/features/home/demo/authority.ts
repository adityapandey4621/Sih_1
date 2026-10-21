export interface ExtractedField {
  id: string
  label: string
  /** What the OCR step proposed. */
  proposed: string
  /** What the scanned document actually says (shown on the scan so the officer can compare). */
  actual: string
}

export const EXTRACTED_FIELDS: readonly ExtractedField[] = [
  { id: 'lessor', label: 'Lessor', proposed: 'Meridian Land Holdings', actual: 'Meridian Land Holdings' },
  { id: 'start', label: 'Valid from', proposed: '01 Apr 2018', actual: '01 Apr 2016' },
  { id: 'end', label: 'Valid until', proposed: '31 Mar 2036', actual: '31 Mar 2036' },
]

export const SCAN_LINES = [
  'LEASE DEED (demo scan)',
  'Lessor: Meridian Land Holdings',
  'Lessee: Northfield Institute of Technology',
  'Lease commences 01 Apr 2016',
  'and remains valid until 31 Mar 2036',
] as const
