import { Fragment } from 'react'
import { motion } from 'motion/react'
import { stagger, wordRise } from '../../animation/variants'

interface SplitHeadingProps {
  text: string
  as?: 'h1' | 'h2'
  id?: string
  className?: string
  /** 'mount' plays immediately; 'view' plays when scrolled into view. */
  trigger?: 'mount' | 'view'
  delay?: number
  /** Words (by index) rendered in italic accent style. */
  accent?: readonly number[]
}

/** Masked word-by-word reveal. The full text stays available to assistive tech. */
export function SplitHeading({
  text,
  as = 'h2',
  id,
  className,
  trigger = 'view',
  delay = 0,
  accent = [],
}: SplitHeadingProps) {
  const Tag = motion[as]
  const words = text.split(' ')
  const play =
    trigger === 'mount'
      ? { initial: 'hidden', animate: 'visible' }
      : { initial: 'hidden', whileInView: 'visible', viewport: { once: true, margin: '-12% 0px' } }

  return (
    <Tag id={id} className={className} variants={stagger(0.07, delay)} aria-label={text} {...play}>
      {words.map((word, i) => (
        <Fragment key={`${word}-${i}`}>
          <span className="split__mask" aria-hidden="true">
            <motion.span
              className="split__word"
              data-accent={accent.includes(i) || undefined}
              variants={wordRise}
            >
              {word}
            </motion.span>
          </span>
          {i < words.length - 1 ? ' ' : null}
        </Fragment>
      ))}
    </Tag>
  )
}


