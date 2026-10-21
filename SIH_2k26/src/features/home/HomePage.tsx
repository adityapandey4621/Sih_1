import { useScroll } from 'motion/react'
import { usePointerParallax } from '../../animation/hooks/usePointerParallax'
import { SceneBackdrop } from './components/SceneBackdrop'
import { HeroSection } from './sections/HeroSection'
import { JourneySection } from './sections/JourneySection'
import { DeadlinesSection } from './sections/DeadlinesSection'
import { ComplianceSection } from './sections/ComplianceSection'
import { AuditSection } from './sections/AuditSection'
import { AuthoritySection } from './sections/AuthoritySection'
import { ClosingSection } from './sections/ClosingSection'
import './home.css'
import './scenes.css'

export default function HomePage() {
  const { scrollYProgress } = useScroll()
  const pointer = usePointerParallax()

  return (
    <>
      <SceneBackdrop progress={scrollYProgress} />
      <main className="home">
        <HeroSection pointer={pointer} />
        <JourneySection />
        <DeadlinesSection />
        <ComplianceSection />
        <AuditSection />
        <AuthoritySection />
        <ClosingSection />
      </main>
    </>
  )
}

