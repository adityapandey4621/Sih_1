import { motion, useReducedMotion, useTransform, type MotionValue } from 'motion/react'
import type { PointerParallax } from '../../../animation/hooks/usePointerParallax'
import { StatusPill } from '../../../components/ui/StatusPill'
import { DepthLayer } from './DepthLayer'

interface DossierVisualProps {
  pointer: PointerParallax
  /** Hero scroll progress, 0 → 1 as the hero leaves the viewport. */
  scroll: MotionValue<number>
}

const SATELLITES = [
  { label: 'Scrutiny', className: 'sat--scrutiny', depth: 22, shift: -60 },
  { label: 'EVC', className: 'sat--evc', depth: -16, shift: -120 },
  { label: 'SAC / SHC', className: 'sat--sac', depth: 28, shift: 90 },
  { label: 'Decision', className: 'sat--decision', depth: -20, shift: 140 },
] as const

const LINKS = [
  'M50 50 C 38 36, 26 30, 15 20',
  'M50 50 C 64 36, 76 28, 86 17',
  'M50 50 C 66 62, 80 70, 88 80',
  'M50 50 C 38 64, 24 74, 13 84',
] as const

const STAGE_ROWS = [
  { name: 'Submitted', state: 'done' },
  { name: 'Scrutiny', state: 'active' },
  { name: 'Re-scrutiny · if returned', state: 'conditional' },
  { name: 'EVC', state: 'next' },
  { name: 'SAC / SHC', state: 'next' },
  { name: 'Authority decision', state: 'next' },
] as const

/** Hero object: a layered application dossier whose cover opens to reveal its stages, linked to the workflow. */
export function DossierVisual({ pointer, scroll }: DossierVisualProps) {
  const reduced = useReducedMotion()
  const rotateY = useTransform(pointer.x, (v) => -14 + v * 9)
  const rotateX = useTransform(pointer.y, (v) => 7 + v * -6)
  const sceneRotate = useTransform(scroll, [0, 1], [0, -26])
  const sceneY = useTransform(scroll, [0, 1], [0, -90])
  const sceneScale = useTransform(scroll, [0, 1], [1, 0.88])
  const sceneOpacity = useTransform(scroll, [0, 0.85], [1, 0])

  return (
    <motion.div
      className="dossier-scene"
      style={reduced ? undefined : { rotateY: sceneRotate, y: sceneY, scale: sceneScale, opacity: sceneOpacity }}
    >
      <svg className="dossier-links" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {LINKS.map((d, i) => (
          <g key={d}>
            <path className="link-base" d={d} />
            <path className="link-pulse" d={d} pathLength={100} style={{ animationDelay: `${-i * 0.9}s` }} />
          </g>
        ))}
      </svg>

      <div className="dossier-pos">
        <div className="dossier-float">
          <motion.div className="dossier" style={{ rotateY, rotateX }}>
            <span className="dossier__frame" aria-hidden="true" />
            <span className="sheet sheet--back2" aria-hidden="true" />
            <span className="sheet sheet--back1" aria-hidden="true" />

            <div className="sheet sheet--front">
              <div className="sheet__inner">
              <div className="sheet__row">
                <span className="sheet__kicker">Application dossier</span>
                <StatusPill tone="active">In scrutiny</StatusPill>
              </div>
              <p className="sheet__id">AICTE-2026-0042</p>
              <p className="sheet__inst">Northfield Institute of Technology <em>(fictional)</em></p>
              <dl className="sheet__meta">
                <div><dt>Submitted</dt><dd>02 Oct 2026</dd></div>
                <div><dt>Route</dt><dd>Per APH &amp; category</dd></div>
              </dl>
              <ol className="sheet__stages" aria-label="Processing stages">
                {STAGE_ROWS.map((row) => (
                  <li key={row.name} data-state={row.state}>
                    <span aria-hidden="true" />
                    {row.name}
                  </li>
                ))}
              </ol>
              </div>
            </div>

            <motion.div
              className="dossier__cover"
              initial={{ rotateY: 0 }}
              animate={{ rotateY: -118 }}
              transition={{ delay: 1.1, duration: 1.8, ease: [0.22, 1, 0.36, 1] }}
            >
              <motion.div
                className="dossier__cover-text"
                initial={{ opacity: 1 }}
                animate={{ opacity: 0 }}
                transition={{ delay: 1.2, duration: 0.5 }}
              >
                <span>Application dossier</span>
                <small>AICTE-2026-0042</small>
              </motion.div>
            </motion.div>
          </motion.div>
        </div>
      </div>

      {SATELLITES.map((sat, i) => (
        <DepthLayer
          key={sat.label}
          className={`sat ${sat.className}`}
          pointer={pointer}
          depth={sat.depth}
          scroll={scroll}
          scrollShift={sat.shift}
        >
          <span className="sat__pill" style={{ animationDuration: `${7 + i * 1.7}s`, animationDelay: `${-i * 2.1}s` }}>
            <span className="sat__dot" aria-hidden="true" />
            {sat.label}
          </span>
        </DepthLayer>
      ))}
      <DepthLayer className="sat sat--returned" pointer={pointer} depth={12} scroll={scroll} scrollShift={-40}>
        <span className="sat__pill sat__pill--conditional" style={{ animationDuration: '9s', animationDelay: '-3s' }}>
          Re-scrutiny · if returned
        </span>
      </DepthLayer>
    </motion.div>
  )
}


