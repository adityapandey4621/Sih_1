import { useRef } from 'react'
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react'
import type { PointerParallax } from '../../../animation/hooks/usePointerParallax'
import { Button } from '../../../components/ui/Button'
import { SplitHeading } from '../../../components/ui/SplitHeading'
import { fadeUp, stagger } from '../../../animation/variants'
import { DossierVisual } from '../components/DossierVisual'
import { HERO } from '../home.constants'

export function HeroSection({ pointer }: { pointer: PointerParallax }) {
  const ref = useRef<HTMLElement>(null)
  const reduced = useReducedMotion()
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end start'] })

  const copyY = useTransform(scrollYProgress, [0, 1], [0, -80])
  const copyOpacity = useTransform(scrollYProgress, [0, 0.6], [1, 0])

  return (
    <section id="top" ref={ref} className="hero" aria-labelledby="hero-title">
      <div className="hero__grid">
        <motion.div className="hero__copy" style={reduced ? undefined : { y: copyY, opacity: copyOpacity }}>
          <motion.div variants={stagger(0.12, 0.15)} initial="hidden" animate="visible">
            <motion.p className="eyebrow" variants={fadeUp}>{HERO.eyebrow}</motion.p>
            <SplitHeading
              as="h1"
              id="hero-title"
              text={HERO.headline}
              trigger="mount"
              delay={0.3}
              accent={HERO.accentWords}
              className="hero__title"
            />
            <motion.p className="hero__text" variants={fadeUp}>{HERO.description}</motion.p>
            <motion.div className="hero__actions" variants={fadeUp}>
              <Button href="#journey">Walk the application journey</Button>
              <Button href="#audit" variant="ghost">See the audit trail</Button>
            </motion.div>
            <motion.p className="hero__note" variants={fadeUp}>{HERO.note}</motion.p>
          </motion.div>
        </motion.div>

        <div className="hero__visual">
          <DossierVisual pointer={pointer} scroll={scrollYProgress} />
        </div>
      </div>

      <a className="cue" href="#journey" aria-label="Scroll to the application journey">
        <span className="cue__label">Scroll</span>
        <span className="cue__line" />
      </a>
    </section>
  )
}
