import { useRef, type ReactNode } from 'react'
import { motion, useScroll, type MotionValue } from 'motion/react'
import { SplitHeading } from '../../../components/ui/SplitHeading'
import { ScrollStage } from '../../../components/ui/ScrollStage'
import { fadeUp } from '../../../animation/variants'
import type { SceneCopy } from '../home.constants'

interface SceneSectionProps {
  copy: SceneCopy
  /** Scene progress runs 0 → 1 while the section crosses the viewport; 0.5 is centred. */
  children: (progress: MotionValue<number>) => ReactNode
}

const inView = { initial: 'hidden', whileInView: 'visible', viewport: { once: true, margin: '-10% 0px' } } as const

/** Shared scene frame: editorial header, a scroll-staged visual, and the honesty note. */
export function SceneSection({ copy, children }: SceneSectionProps) {
  const ref = useRef<HTMLElement>(null)
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'end start'] })
  const titleId = `${copy.id}-title`

  return (
    <section id={copy.id} ref={ref} className="scene" aria-labelledby={titleId}>
      <div className="scene__inner">
        <header className="scene__head">
          <motion.p className="eyebrow" variants={fadeUp} {...inView}>{copy.eyebrow}</motion.p>
          <SplitHeading id={titleId} text={copy.headline} accent={copy.accent} className="scene__title" />
          <motion.p className="scene__intro" variants={fadeUp} {...inView}>{copy.intro}</motion.p>
        </header>
        <ScrollStage progress={scrollYProgress}>{children(scrollYProgress)}</ScrollStage>
        <motion.p className="scene__note" variants={fadeUp} {...inView}>
          <span aria-hidden="true">※</span> {copy.note}
        </motion.p>
      </div>
    </section>
  )
}
