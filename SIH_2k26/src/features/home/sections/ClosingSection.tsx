import { motion } from 'motion/react'
import { SplitHeading } from '../../../components/ui/SplitHeading'
import { Button } from '../../../components/ui/Button'
import { fadeUp, stagger } from '../../../animation/variants'
import { CLOSING } from '../home.constants'

export function ClosingSection() {
  return (
    <section id="summary" className="scene closing" aria-labelledby="closing-title">
      <div className="scene__inner">
        <SplitHeading id="closing-title" text={CLOSING.headline} accent={CLOSING.accent} className="scene__title closing__title" />
        <motion.ul
          className="closing__list"
          variants={stagger(0.1)}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-10% 0px' }}
        >
          {CLOSING.items.map((item, i) => (
            <motion.li key={item.title} variants={fadeUp}>
              <a className="closing__item" href={item.href}>
                <span className="closing__num" aria-hidden="true">0{i + 1}</span>
                <span className="closing__body">
                  <strong>{item.title}</strong>
                  <span>{item.copy}</span>
                </span>
                <span className="closing__go" aria-hidden="true">→</span>
              </a>
            </motion.li>
          ))}
        </motion.ul>
        <div className="closing__actions">
          <Button href="#top">Back to overview</Button>
          <Button href="#journey" variant="ghost">Replay the journey</Button>
        </div>
      </div>
    </section>
  )
}
